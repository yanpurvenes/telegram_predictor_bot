import telegram
from telegram import Update
from telegram.constants import ParseMode # <--- ИЗМЕНЕНИЕ ЗДЕСЬ
from telegram.ext import Application, CommandHandler, CallbackContext, MessageHandler, filters
import os
import json
import random
import asyncio
import datetime
import pytz # Для работы с часовыми поясами
from dotenv import load_dotenv # Для локальной разработки, на Railway не помешает

# Загрузка переменных окружения (если есть .env файл, для локального теста)
# На Railway переменные будут браться из настроек сервиса
load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TARGET_CHANNEL_ID_STR = os.getenv("TARGET_CHANNEL_ID")
ADMIN_USER_ID_STR = os.getenv("ADMIN_USER_ID")
SCHEDULE_HOUR_STR = os.getenv("SCHEDULE_HOUR", "22") # По умолчанию 22
SCHEDULE_MINUTE_STR = os.getenv("SCHEDULE_MINUTE", "0")  # По умолчанию 00

# Проверка наличия обязательных переменных (важно для Railway)
if not TELEGRAM_BOT_TOKEN:
    # В логах Railway будет видно, если переменная не установлена
    print("ОШИБКА: Переменная окружения TELEGRAM_BOT_TOKEN не установлена!") 
    # Можно остановить выполнение, если критично, но Railway может перезапускать
    # Для простоты сейчас оставим так, но лучше бы бот не стартовал без токена.
    # raise ValueError("Необходимо установить переменную окружения TELEGRAM_BOT_TOKEN")
if not TARGET_CHANNEL_ID_STR:
    print("ОШИБКА: Переменная окружения TARGET_CHANNEL_ID не установлена!")
    # raise ValueError("Необходимо установить переменную окружения TARGET_CHANNEL_ID")

TARGET_CHANNEL_ID = None
if TARGET_CHANNEL_ID_STR:
    try:
        TARGET_CHANNEL_ID = int(TARGET_CHANNEL_ID_STR)
    except ValueError:
        print(f"ОШИБКА: TARGET_CHANNEL_ID ('{TARGET_CHANNEL_ID_STR}') должен быть числом!")
        # raise ValueError("TARGET_CHANNEL_ID должен быть числом")

ADMIN_USER_ID = int(ADMIN_USER_ID_STR) if ADMIN_USER_ID_STR and ADMIN_USER_ID_STR.isdigit() else None
SCHEDULE_HOUR = int(SCHEDULE_HOUR_STR) if SCHEDULE_HOUR_STR and SCHEDULE_HOUR_STR.isdigit() else 22
SCHEDULE_MINUTE = int(SCHEDULE_MINUTE_STR) if SCHEDULE_MINUTE_STR and SCHEDULE_MINUTE_STR.isdigit() else 0

all_predictions = []
known_users_data = {} # {user_id: {"username": "uname", "first_name": "Fname", "id": 123}}
predictions_sent_today_ids = set()
USERS_DATA_FILE = "known_users.json" # Файл для сохранения известных пользователей

def load_predictions_from_file(filename="quotes_1000.json"):
    global all_predictions
    try:
        # На Railway файл будет в корне проекта
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
            # Преобразуем ключи из строк (json) в int
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
    # Используем username если есть, иначе first_name
    mention_name = user_info.get('username', user_info.get('first_name', str(user_id))) 

    if user_info.get('username'):
        mention = f"@{user_info['username']}"
    else:
        # Markdown ссылка для упоминания пользователя без username
        # Используем first_name, если он есть, иначе user_id
        display_name = user_info.get('first_name', f"User {user_id}")
        mention = f"[{display_name.replace('[', '').replace(']', '')}](tg://user?id={user_id})" # Экранируем скобки в имени
    
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
        # Более широкий список ключевых слов для определения проблем с пользователем/чатом
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
    except Exception as e: # Ловим другие возможные ошибки
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
             except Exception as e: print(f"Не удалось уведомить администратора: {e}")
        return
    
    load_known_users() # Загружаем актуальный список пользователей перед каждой рассылкой

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
            except Exception as e: print(f"Не удалось уведомить администратора: {e}")
        return

    print(f"Начинаем рассылку для {len(users_to_message)} пользователей. Доступно {len(available_predictions_for_today)} предсказаний.")

    for user_info in users_to_message:
        if not available_predictions_for_today:
            print("Уникальные предсказания на сегодня закончились, не все пользователи их получили.")
            if ADMIN_USER_ID:
                try:
                    await context.bot.send_message(chat_id=ADMIN_USER_ID, text="Уникальные предсказания на сегодня закончились!")
                except Exception as e: print(f"Не удалось уведомить администратора: {e}")
            break

        current_prediction = available_predictions_for_today.pop(0)
        success = await send_prediction_to_user(context.bot, user_info, current_prediction)
        
        if success:
            predictions_sent_today_ids.add(current_prediction['id'])
            print(f"Осталось {len(available_predictions_for_today)} уникальных предсказаний на сегодня.")
            await asyncio.sleep(30) # Задержка 30 секунд
        else:
            # Если отправка не удалась, предсказание не добавляется в predictions_sent_today_ids
            # и может быть использовано для другого пользователя (если проблема была не в предсказании).
            # Пользователь мог быть удален из known_users_data внутри send_prediction_to_user.
            print(f"Пропуск задержки для пользователя из-за ошибки отправки.")
    
    print("Ежедневная рассылка завершена.")

async def store_user_from_channel_message(update: Update, context: CallbackContext):
    if not update.message or not update.message.from_user or not TARGET_CHANNEL_ID:
        return 

    user = update.message.from_user
    if user.is_bot: # Не сохраняем других ботов
        return

    # Проверяем, что сообщение из целевого канала
    if update.message.chat_id != TARGET_CHANNEL_ID:
        return

    # Обновляем данные, если пользователь уже есть, но изменились username/first_name/last_name, или добавляем нового
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
        save_known_users() # Сохраняем в файл при обновлении или добавлении
        print(f"Пользователь {user.first_name} (ID: {user_id_int}, Username: @{user.username}) "
              f"обнаружен/обновлен в канале {TARGET_CHANNEL_ID}. "
              f"Всего пользователей в памяти: {len(known_users_data)}")

async def start_command(update: Update, context: CallbackContext):
    user = update.effective_user
    await update.message.reply_text(
        f"Привет, {user.first_name}! Я бот предсказаний для канала (ID: {TARGET_CHANNEL_ID}).\n"
        f"Предсказания отправляются активным участникам этого канала ежедневно в {SCHEDULE_HOUR:02d}:{SCHEDULE_MINUTE:02d} МСК.\n"
        "Чтобы получать предсказания, просто будьте участником указанного канала и проявляйте там активность (пишите сообщения). "
        "Ваши данные (ID, имя, юзернейм) будут сохранены для этой цели."
    )

async def help_command(update: Update, context: CallbackContext):
    help_text = (
        "Я бот для отправки ежедневных предсказаний в канал.\n"
        "Предсказания получают пользователи, которые пишут сообщения в целевом канале.\n\n"
        "Команды (в личных сообщениях со мной):\n"
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

async def list_users_command(update: Update, context: CallbackContext):
    user = update.effective_user
    if not (ADMIN_USER_ID and user and user.id == ADMIN_USER_ID):
        await update.message.reply_text("Эта команда доступна только администратору бота.")
        return

    load_known_users() # Обновим список перед показом
    if not known_users_data:
        await update.message.reply_text("Список отслеживаемых пользователей пуст (никто еще не писал в целевом канале, либо файл не загружен).")
        return

    message = "Пользователи, для которых будут отправляться предсказания (из known_users.json):\n"
    for user_id, info in known_users_data.items():
        name = f"@{info['username']}" if info.get('username') else info.get('first_name', f'User {user_id}')
        message += f"- {name} (ID: {user_id})\n"
    
    max_len = 4096 # Максимальная длина сообщения Telegram
    for i in range(0, len(message), max_len):
        await update.message.reply_text(message[i:i+max_len])

async def force_send_command(update: Update, context: CallbackContext):
    user = update.effective_user
    if not (ADMIN_USER_ID and user and user.id == ADMIN_USER_ID):
        await update.message.reply_text("Эта команда доступна только администратору бота.")
        return
    
    if not TARGET_CHANNEL_ID:
        await update.message.reply_text("TARGET_CHANNEL_ID не установлен. Не могу запустить рассылку.")
        return

    await update.message.reply_text("Принудительный запуск рассылки предсказаний...")
    await daily_prediction_job(context) # context.bot должен быть доступен

async def error_handler(update: object, context: CallbackContext) -> None:
    """Log the error and send a telegram message to notify the developer if ADMIN_USER_ID is set."""
    print(f"Исключение при обработке обновления: {context.error}")
    if ADMIN_USER_ID:
        try:
            # Формируем сообщение об ошибке
            error_message = f"Произошла ошибка в боте:\n"
            error_message += f"Error: {context.error}\n"
            if update and isinstance(update, Update):
                error_message += f"Update: {update.to_json()[:1000]}\n" # Ограничиваем длину апдейта
            
            # Обрезаем сообщение, если оно слишком длинное
            max_len_error = 4000 
            if len(error_message) > max_len_error:
                error_message = error_message[:max_len_error] + "\n... (сообщение обрезано)"

            await context.bot.send_message(chat_id=ADMIN_USER_ID, text=error_message)
        except Exception as e:
            print(f"Не удалось отправить сообщение об ошибке администратору: {e}")

def main():
    """Основная функция запуска бота."""
    print("Запуск main функции...")
    if not TELEGRAM_BOT_TOKEN or not TARGET_CHANNEL_ID_STR: # Проверяем еще раз перед созданием Application
        print("КРИТИЧЕСКАЯ ОШИБКА: Отсутствуют TELEGRAM_BOT_TOKEN или TARGET_CHANNEL_ID. Бот не может быть запущен.")
        return

    print("Загрузка данных...")
    load_predictions_from_file()
    load_known_users() # Загрузка пользователей при старте

    print("Создание экземпляра Application...")
    # application_builder = Application.builder().token(TELEGRAM_BOT_TOKEN)
    # Для более точного отслеживания, какие апдейты мы хотим получать:
    # allowed_updates = [Update.MESSAGE, Update.CALLBACK_QUERY, Update.MY_CHAT_MEMBER, Update.CHAT_MEMBER]
    # application_builder.allowed_updates(allowed_updates)
    # application = application_builder.build()
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()


    # Регистрация обработчиков команд (для ЛС бота)
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("list_users", list_users_command))
    application.add_handler(CommandHandler("force_send", force_send_command))

    # ОБРАБОТЧИК СООБЩЕНИЙ ИЗ ЦЕЛЕВОГО КАНАЛА
    if TARGET_CHANNEL_ID: # Добавляем обработчик только если ID канала задан
        application.add_handler(MessageHandler(
            filters.Chat(chat_id=TARGET_CHANNEL_ID) & filters.TEXT & (~filters.COMMAND) & (~filters.UpdateType.EDITED_MESSAGE),
            store_user_from_channel_message
        ))
        print(f"Настроен обработчик сообщений для канала ID: {TARGET_CHANNEL_ID}")
    else:
        print("Обработчик сообщений из канала не настроен, так как TARGET_CHANNEL_ID не задан.")
    
    # Обработчик ошибок
    application.add_error_handler(error_handler)

    # Настройка ежедневной задачи
    job_queue = application.job_queue
    
    moscow_tz = pytz.timezone('Europe/Moscow')
    # Убедимся, что время для планировщика - это объект datetime.time
    target_time_dt = datetime.time(hour=SCHEDULE_HOUR, minute=SCHEDULE_MINUTE, tzinfo=moscow_tz)
    
    job_queue.run_daily(daily_prediction_job, time=target_time_dt, name="daily_predictions_job")
    
    print(f"Бот настроен. Ежедневная рассылка в {SCHEDULE_HOUR:02d}:{SCHEDULE_MINUTE:02d} по московскому времени.")
    print(f"Текущее время по МСК: {datetime.datetime.now(moscow_tz).strftime('%H:%M:%S %Z')}")
    if ADMIN_USER_ID:
        print(f"ID администратора: {ADMIN_USER_ID}")

    # Запуск бота
    print("Запуск бота (polling)...")
# ... (другие обработчики команд list_users, force_send)

        # ТЕСТОВЫЙ ОБРАБОТЧИК КОМАНДЫ ДЛЯ КАНАЛА
        async def test_channel_command(update: Update, context: CallbackContext):
            print(f"!!! ПОЛУЧЕНА КОМАНДА /testchannel В ЧАТЕ {update.message.chat_id} ОТ ПОЛЬЗОВАТЕЛЯ {update.message.from_user.id if update.message.from_user else 'Неизвестно'} !!!")
            try:
                await update.message.reply_text(f"Тестовая команда из чата {update.message.chat_id} получена!")
                print(f"!!! Ответ на /testchannel отправлен в чат {update.message.chat_id} !!!")
            except Exception as e:
                print(f"!!! Ошибка при ответе на /testchannel в чате {update.message.chat_id}: {e} !!!")

        if TARGET_CHANNEL_ID: # Добавляем только если ID канала задан
            # Важно: фильтр filters.Chat(chat_id=TARGET_CHANNEL_ID) должен быть точным
            application.add_handler(CommandHandler("testchannel", test_channel_command, filters=filters.Chat(chat_id=TARGET_CHANNEL_ID)))
            print(f"Добавлен тестовый обработчик /testchannel для чата (канала/группы) с ID: {TARGET_CHANNEL_ID}")
        else:
            print("Тестовый обработчик /testchannel НЕ добавлен, так как TARGET_CHANNEL_ID не задан.")

        # Обработчик ошибок
        application.add_error_handler(error_handler) # Убедитесь, что он есть

        # Настройка ежедневной задачи
        job_queue = application.job_queue
        # ... (остальной код job_queue) ...

        print("Запуск бота (polling)...")
        application.run_polling(allowed_updates=Update.ALL_TYPES)
    application.run_polling(allowed_updates=Update.ALL_TYPES) # Указываем, какие обновления получать

if __name__ == '__main__':
    main()
