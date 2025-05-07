import telegram
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, CallbackContext, MessageHandler, filters
import os
import json
import random
import asyncio
import datetime
import pytz 
from dotenv import load_dotenv

# Загрузка переменных окружения
load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TARGET_CHANNEL_ID_STR = os.getenv("TARGET_CHANNEL_ID")
ADMIN_USER_ID_STR = os.getenv("ADMIN_USER_ID")
SCHEDULE_HOUR_STR = os.getenv("SCHEDULE_HOUR", "22")
SCHEDULE_MINUTE_STR = os.getenv("SCHEDULE_MINUTE", "0")

# --- Начальная диагностика переменных окружения ---
print("--- Начало диагностики переменных окружения ---")
print(f"TELEGRAM_BOT_TOKEN: {'Задан' if TELEGRAM_BOT_TOKEN else 'НЕ ЗАДАН'}")
print(f"TARGET_CHANNEL_ID_STR: {TARGET_CHANNEL_ID_STR if TARGET_CHANNEL_ID_STR else 'НЕ ЗАДАН'}")
print(f"ADMIN_USER_ID_STR: {ADMIN_USER_ID_STR if ADMIN_USER_ID_STR else 'НЕ ЗАДАН'}")
print(f"SCHEDULE_HOUR_STR: {SCHEDULE_HOUR_STR}")
print(f"SCHEDULE_MINUTE_STR: {SCHEDULE_MINUTE_STR}")
print("--- Конец диагностики переменных окружения ---")


if not TELEGRAM_BOT_TOKEN:
    print("ОШИБКА: Переменная окружения TELEGRAM_BOT_TOKEN не установлена!")
if not TARGET_CHANNEL_ID_STR:
    print("ОШИБКА: Переменная окружения TARGET_CHANNEL_ID не установлена!")

TARGET_CHANNEL_ID = None
if TARGET_CHANNEL_ID_STR:
    try:
        TARGET_CHANNEL_ID = int(TARGET_CHANNEL_ID_STR)
    except ValueError:
        print(f"ОШИБКА: TARGET_CHANNEL_ID ('{TARGET_CHANNEL_ID_STR}') должен быть числом!")

ADMIN_USER_ID = int(ADMIN_USER_ID_STR) if ADMIN_USER_ID_STR and ADMIN_USER_ID_STR.isdigit() else None
SCHEDULE_HOUR = int(SCHEDULE_HOUR_STR) if SCHEDULE_HOUR_STR and SCHEDULE_HOUR_STR.isdigit() else 22
SCHEDULE_MINUTE = int(SCHEDULE_MINUTE_STR) if SCHEDULE_MINUTE_STR and SCHEDULE_MINUTE_STR.isdigit() else 0

all_predictions = []
known_users_data = {}
predictions_sent_today_ids = set()
USERS_DATA_FILE = "known_users.json"

def load_predictions_from_file(filename="quotes_1000.json"):
    global all_predictions
    try:
        with open(filename, 'r', encoding='utf-8') as f:
            all_predictions = json.load(f)
        print(f"Загружено {len(all_predictions)} предсказаний из {filename}.")
    except FileNotFoundError:
        print(f"Файл предсказаний {filename} не найден. Предсказания не будут отправляться.")
        all_predictions = []
    except json.JSONDecodeError:
        print(f"Ошибка декодирования JSON в файле {filename}. Проверьте его структуру.")
        all_predictions = []

def save_known_users():
    global known_users_data
    try:
        with open(USERS_DATA_FILE, 'w', encoding='utf-8') as f:
            json.dump(known_users_data, f, ensure_ascii=False, indent=4)
        print(f"Данные пользователей сохранены в {USERS_DATA_FILE} ({len(known_users_data)} пользователей)")
    except Exception as e:
        print(f"Ошибка сохранения данных пользователей в {USERS_DATA_FILE}: {e}")

def load_known_users():
    global known_users_data
    if not os.path.exists(USERS_DATA_FILE):
        print(f"Файл {USERS_DATA_FILE} не найден. Начинаем с пустого списка пользователей.")
        known_users_data = {}
        return
    try:
        with open(USERS_DATA_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
            known_users_data = {int(k): v for k, v in data.items()}
        print(f"Загружено {len(known_users_data)} пользователей из {USERS_DATA_FILE}")
    except json.JSONDecodeError:
        print(f"Ошибка декодирования JSON в {USERS_DATA_FILE}. Начинаем с пустого списка.")
        known_users_data = {}
    except Exception as e:
        print(f"Не удалось загрузить данные пользователей из {USERS_DATA_FILE}: {e}")
        known_users_data = {}

async def send_prediction_to_user(bot: telegram.Bot, user_info: dict, prediction: dict):
    user_id = user_info['id']
    mention_name = user_info.get('username', user_info.get('first_name', str(user_id)))

    if user_info.get('username'):
        mention = f"@{user_info['username']}"
    else:
        display_name = user_info.get('first_name', f"User {user_id}")
        mention = f"[{display_name.replace('[', '').replace(']', '')}](tg://user?id={user_id})"
    
    message_text = f"{mention}, ваше предсказание на сегодня: {prediction['text']}"

    try:
        await bot.send_message(
            chat_id=TARGET_CHANNEL_ID,
            text=message_text,
            parse_mode=ParseMode.MARKDOWN
        )
        print(f"Предсказание (ID: {prediction['id']}) отправлено для {mention_name} (ID: {user_id}) в канал {TARGET_CHANNEL_ID}")
        return True
    except telegram.error.TelegramError as e:
        print(f"Ошибка отправки предсказания для {mention_name} (ID: {user_id}) в канал {TARGET_CHANNEL_ID}: {e}")
        error_text = str(e).lower()
        if any(err_keyword in error_text for err_keyword in [
            "user not found", "chat member not found", "bot was kicked", 
            "user is deactivated", "chat not found", "group chat was deactivated",
            "have no rights to send a message", "user restricted", "bot was blocked by the user"
        ]):
            if user_id in known_users_data:
                print(f"Пользователь {mention_name} (ID: {user_id}) не найден/деактивирован/покинул канал/заблокировал бота или проблема с правами в канале. Удаляем из списка рассылки.")
                del known_users_data[user_id]
                save_known_users()
        return False
    except Exception as e:
        print(f"Непредвиденная ошибка при отправке сообщения пользователю {user_id}: {e}")
        return False

async def daily_prediction_job(context: CallbackContext):
    global predictions_sent_today_ids
    
    print(f"Запуск ежедневной рассылки предсказаний... Время: {datetime.datetime.now(pytz.timezone('Europe/Moscow'))}")
    predictions_sent_today_ids.clear()

    if not TARGET_CHANNEL_ID:
        print("TARGET_CHANNEL_ID не установлен. Рассылка невозможна.")
        return

    if not all_predictions:
        print("Нет доступных предсказаний для отправки.")
        if ADMIN_USER_ID:
             try:
                await context.bot.send_message(chat_id=ADMIN_USER_ID, text="Администратору: Список предсказаний пуст (quotes_1000.json). Не могу начать рассылку.")
             except Exception as e: print(f"Не удалось уведомить администратора (нет предсказаний): {e}")
        return
    
    load_known_users()

    if not known_users_data:
        print("Нет известных пользователей для отправки предсказаний (никто не писал в канале, либо файл known_users.json пуст/недоступен).")
        return

    users_to_message = list(known_users_data.values())
    random.shuffle(users_to_message)
    
    available_predictions_for_today = [p for p in all_predictions if p['id'] not in predictions_sent_today_ids]
    random.shuffle(available_predictions_for_today)
    
    if not available_predictions_for_today:
        print("Все предсказания уже были использованы или их нет (для сегодняшней сессии).")
        if ADMIN_USER_ID:
            try:
                await context.bot.send_message(chat_id=ADMIN_USER_ID, text="Кажется, на сегодня все предсказания уже были розданы или закончились!")
            except Exception as e: print(f"Не удалось уведомить администратора (предсказания закончились): {e}")
        return

    print(f"Начинаем рассылку для {len(users_to_message)} пользователей. Доступно {len(available_predictions_for_today)} предсказаний.")

    for user_info in users_to_message:
        if not available_predictions_for_today:
            print("Уникальные предсказания на сегодня закончились, не все пользователи их получили.")
            if ADMIN_USER_ID:
                try:
                    await context.bot.send_message(chat_id=ADMIN_USER_ID, text="Уникальные предсказания на сегодня закончились!")
                except Exception as e: print(f"Не удалось уведомить администратора (уникальные предсказания закончились): {e}")
            break

        current_prediction = available_predictions_for_today.pop(0)
        success = await send_prediction_to_user(context.bot, user_info, current_prediction)
        
        if success:
            predictions_sent_today_ids.add(current_prediction['id'])
            print(f"Осталось {len(available_predictions_for_today)} уникальных предсказаний на сегодня.")
            await asyncio.sleep(30)
        else:
            print(f"Пропуск задержки для пользователя из-за ошибки отправки.")
    
    print("Ежедневная рассылка завершена.")

async def store_user_from_channel_message(update: Update, context: CallbackContext):
    print(f"--- store_user_from_channel_message ВЫЗВАНА для чата {update.message.chat_id if update.message else 'нет сообщения'} ---")

    if not update.message or not update.message.from_user or not TARGET_CHANNEL_ID:
        print(f"--- store_user_from_channel_message: Прерывание (нет сообщения, пользователя или TARGET_CHANNEL_ID). "
              f"update.message: {'Есть' if update.message else 'Нет'}, "
              f"update.message.from_user: {'Есть' if update.message and update.message.from_user else 'Нет'}, "
              f"TARGET_CHANNEL_ID: {TARGET_CHANNEL_ID}")
        return 

    user = update.message.from_user
    if user.is_bot:
        print(f"--- store_user_from_channel_message: Прерывание (сообщение от бота {user.id}) ---")
        return

    if update.message.chat_id != TARGET_CHANNEL_ID:
        print(f"--- store_user_from_channel_message: Прерывание (сообщение из другого чата: {update.message.chat_id}, ожидался {TARGET_CHANNEL_ID}) ---")
        return
    
    print(f"--- store_user_from_channel_message: Сообщение из целевого чата {TARGET_CHANNEL_ID} от пользователя {user.id} ({user.first_name} @{user.username}) ---")

    user_id_int = user.id
    if user_id_int not in known_users_data or \
       known_users_data[user_id_int].get('username') != user.username or \
       known_users_data[user_id_int].get('first_name') != user.first_name or \
       known_users_data[user_id_int].get('last_name') != user.last_name:
        
        known_users_data[user_id_int] = {
            "id": user_id_int,
            "first_name": user.first_name,
            "last_name": user.last_name,
            "username": user.username
        }
        save_known_users()
        print(f"Пользователь {user.first_name} (ID: {user_id_int}, Username: @{user.username}) "
              f"обнаружен/обновлен в канале {TARGET_CHANNEL_ID}. "
              f"Всего пользователей в памяти: {len(known_users_data)}")
    else:
        print(f"--- store_user_from_channel_message: Данные пользователя {user_id_int} уже актуальны, не сохраняем. ---")

async def start_command(update: Update, context: CallbackContext):
    user = update.effective_user
    print(f"!!! ПОЛУЧЕНА КОМАНДА /start от пользователя {user.id} ({user.first_name}) в ЛС !!!")
    await update.message.reply_text(
        f"Привет, {user.first_name}! Я бот предсказаний для канала (ID: {TARGET_CHANNEL_ID}).\n"
        f"Предсказания отправляются активным участникам этого канала ежедневно в {SCHEDULE_HOUR:02d}:{SCHEDULE_MINUTE:02d} МСК.\n"
        "Чтобы получать предсказания, просто будьте участником указанного канала и проявляйте там активность (пишите сообщения). "
        "Ваши данные (ID, имя, юзернейм) будут сохранены для этой цели."
    )
    print(f"!!! Ответ на /start отправлен пользователю {user.id} !!!")


async def help_command(update: Update, context: CallbackContext):
    user = update.effective_user
    print(f"!!! ПОЛУЧЕНА КОМАНДА /help от пользователя {user.id} ({user.first_name}) в ЛС !!!")
    help_text = (
        "Я бот для отправки ежедневных предсказаний в канал.\n"
        "Предсказания получают пользователи, которые пишут сообщения в целевом канале.\n\n"
        "Команды (в личных сообщениях со мной):\n"
        "/ping - Проверить, что я жив.\n"
        "/start - Информация о боте.\n"
        "/help - Показать это сообщение.\n"
    )
    if ADMIN_USER_ID and update.effective_user and update.effective_user.id == ADMIN_USER_ID:
        help_text += (
            "\nКоманды администратора:\n"
            "/list_users - Показать список пользователей, для которых будут отправляться предсказания.\n"
            "/force_send - Принудительно запустить рассылку (для теста).\n"
        )
    await update.message.reply_text(help_text)
    print(f"!!! Ответ на /help отправлен пользователю {user.id} !!!")


async def list_users_command(update: Update, context: CallbackContext):
    user = update.effective_user
    print(f"!!! ПОЛУЧЕНА КОМАНДА /list_users от пользователя {user.id} ({user.first_name}) в ЛС !!!")
    if not (ADMIN_USER_ID and user and user.id == ADMIN_USER_ID):
        await update.message.reply_text("Эта команда доступна только администратору бота.")
        print(f"!!! Пользователю {user.id} отказано в доступе к /list_users !!!")
        return

    load_known_users()
    if not known_users_data:
        await update.message.reply_text("Список отслеживаемых пользователей пуст (никто еще не писал в целевом канале, либо файл не загружен).")
        return

    message = "Пользователи, для которых будут отправляться предсказания (из known_users.json):\n"
    for user_id_key, info in known_users_data.items(): # Используем user_id_key как ключ словаря
        name = f"@{info['username']}" if info.get('username') else info.get('first_name', f'User {info["id"]}')
        message += f"- {name} (ID: {info['id']})\n" # Берем info['id'] для отображения
    
    max_len = 4096
    for i in range(0, len(message), max_len):
        await update.message.reply_text(message[i:i+max_len])
    print(f"!!! Список пользователей отправлен администратору {user.id} !!!")


async def force_send_command(update: Update, context: CallbackContext):
    user = update.effective_user
    print(f"!!! ПОЛУЧЕНА КОМАНДА /force_send от пользователя {user.id} ({user.first_name}) в ЛС !!!")
    if not (ADMIN_USER_ID and user and user.id == ADMIN_USER_ID):
        await update.message.reply_text("Эта команда доступна только администратору бота.")
        print(f"!!! Пользователю {user.id} отказано в доступе к /force_send !!!")
        return
    
    if not TARGET_CHANNEL_ID:
        await update.message.reply_text("TARGET_CHANNEL_ID не установлен. Не могу запустить рассылку.")
        return

    await update.message.reply_text("Принудительный запуск рассылки предсказаний...")
    print(f"!!! Администратор {user.id} запустил /force_send !!!")
    await daily_prediction_job(context)

async def error_handler(update: object, context: CallbackContext) -> None:
    print(f"Исключение при обработке обновления: {context.error}")
    if ADMIN_USER_ID:
        try:
            error_message = f"Произошла ошибка в боте:\n"
            error_message += f"Error: {context.error}\n"
            if update and isinstance(update, Update):
                error_message += f"Update: {update.to_json()[:1000]}\n"
            
            max_len_error = 4000
            if len(error_message) > max_len_error:
                error_message = error_message[:max_len_error] + "\n... (сообщение обрезано)"

            await context.bot.send_message(chat_id=ADMIN_USER_ID, text=error_message)
        except Exception as e:
            print(f"Не удалось отправить сообщение об ошибке администратору (обработка ошибки): {e}")

# === НОВЫЙ ОБРАБОТЧИК ДЛЯ /ping В ЛС ===
async def ping_command(update: Update, context: CallbackContext):
    """Отвечает на команду /ping в личных сообщениях."""
    user = update.effective_user
    print(f"!!! ПОЛУЧЕНА КОМАНДА /ping от пользователя {user.id} ({user.first_name}) в ЛС !!!")
    await update.message.reply_text(f"Pong! Привет, {user.first_name}! Я жив.")
    print(f"!!! Ответ на /ping отправлен пользователю {user.id} !!!")
# === КОНЕЦ НОВОГО ОБРАБОТЧИКА ===

def main():
    print("Запуск main функции...")
    if not TELEGRAM_BOT_TOKEN or not TARGET_CHANNEL_ID_STR:
        print("КРИТИЧЕСКАЯ ОШИБКА: Отсутствуют TELEGRAM_BOT_TOKEN или TARGET_CHANNEL_ID_STR. Бот не может быть запущен.")
        return

    print("Загрузка данных...")
    load_predictions_from_file()
    load_known_users()

    print("Создание экземпляра Application...")
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # --- РЕГИСТРАЦИЯ ОБРАБОТЧИКОВ ---
    # Сначала обработчик для /ping в ЛС
    application.add_handler(CommandHandler("ping", ping_command)) # Фильтры не нужны, по умолчанию для ЛС
    print("Добавлен обработчик /ping для личных сообщений.")

    # Затем остальные команды для ЛС
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("list_users", list_users_command))
    application.add_handler(CommandHandler("force_send", force_send_command))
    print("Добавлены обработчики /start, /help, /list_users, /force_send.")


    if TARGET_CHANNEL_ID:
        # ОБРАБОТЧИК ОБЫЧНЫХ СООБЩЕНИЙ ИЗ ЦЕЛЕВОГО КАНАЛА/ГРУППЫ
        application.add_handler(MessageHandler(
            filters.Chat(chat_id=TARGET_CHANNEL_ID) & filters.TEXT & (~filters.COMMAND) & (~filters.UpdateType.EDITED_MESSAGE),
            store_user_from_channel_message
        ))
        print(f"Настроен обработчик store_user_from_channel_message для чата ID: {TARGET_CHANNEL_ID}")

        # ТЕСТОВЫЙ ОБРАБОТЧИК КОМАНДЫ ДЛЯ КАНАЛА/ГРУППЫ
        async def test_channel_command(update: Update, context: CallbackContext):
            user_obj = update.message.from_user
            user_id_str = str(user_obj.id) if user_obj else "Неизвестно"
            print(f"!!! ПОЛУЧЕНА КОМАНДА /testchannel В ЧАТЕ {update.message.chat_id} ОТ ПОЛЬЗОВАТЕЛЯ {user_id_str} !!!")
            try:
                reply_text = f"Тестовая команда из чата {update.message.chat_id} получена!"
                if user_obj:
                    reply_text += f" ID пользователя: {user_obj.id}"
                await update.message.reply_text(reply_text)
                print(f"!!! Ответ на /testchannel отправлен в чат {update.message.chat_id} !!!")
            except Exception as e:
                print(f"!!! Ошибка при ответе на /testchannel в чате {update.message.chat_id}: {e} !!!")
        
        application.add_handler(CommandHandler("testchannel", test_channel_command, filters=filters.Chat(chat_id=TARGET_CHANNEL_ID)))
        print(f"Добавлен тестовый обработчик /testchannel для чата с ID: {TARGET_CHANNEL_ID}")
    else:
        print("Обработчики для TARGET_CHANNEL_ID не настроены, так как TARGET_CHANNEL_ID не задан или невалиден.")
    
    application.add_error_handler(error_handler)

    job_queue = application.job_queue
    moscow_tz = pytz.timezone('Europe/Moscow')
    target_time_dt = datetime.time(hour=SCHEDULE_HOUR, minute=SCHEDULE_MINUTE, tzinfo=moscow_tz)
    
    job_queue.run_daily(daily_prediction_job, time=target_time_dt, name="daily_predictions_job")
    
    print(f"Бот настроен. Ежедневная рассылка в {SCHEDULE_HOUR:02d}:{SCHEDULE_MINUTE:02d} по московскому времени.")
    print(f"Текущее время по МСК: {datetime.datetime.now(moscow_tz).strftime('%H:%M:%S %Z')}")
    if ADMIN_USER_ID:
        print(f"ID администратора: {ADMIN_USER_ID}")

    print("Запуск бота (polling)...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
