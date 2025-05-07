import telegram
from telegram import Update, ParseMode
from telegram.ext import Application, CommandHandler, CallbackContext, MessageHandler, filters
import os
import json
import random
import asyncio
import datetime
import pytz # Для работы с часовыми поясами
from dotenv import load_dotenv

# Загрузка переменных окружения
load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TARGET_CHANNEL_ID_STR = os.getenv("TARGET_CHANNEL_ID")
ADMIN_USER_ID_STR = os.getenv("ADMIN_USER_ID")
SCHEDULE_HOUR_STR = os.getenv("SCHEDULE_HOUR", "22") 
SCHEDULE_MINUTE_STR = os.getenv("SCHEDULE_MINUTE", "0")

if not TELEGRAM_BOT_TOKEN:
    raise ValueError("Необходимо установить переменную окружения TELEGRAM_BOT_TOKEN")
if not TARGET_CHANNEL_ID_STR:
    raise ValueError("Необходимо установить переменную окружения TARGET_CHANNEL_ID")

try:
    TARGET_CHANNEL_ID = int(TARGET_CHANNEL_ID_STR)
except ValueError:
    raise ValueError("TARGET_CHANNEL_ID должен быть числом")

ADMIN_USER_ID = int(ADMIN_USER_ID_STR) if ADMIN_USER_ID_STR else None
SCHEDULE_HOUR = int(SCHEDULE_HOUR_STR)
SCHEDULE_MINUTE = int(SCHEDULE_MINUTE_STR)

all_predictions = []
known_users_data = {} 
predictions_sent_today_ids = set()
USERS_DATA_FILE = "known_users.json"

def load_predictions_from_file(filename="quotes_1000.json"):
    global all_predictions
    try:
        with open(filename, 'r', encoding='utf-8') as f:
            all_predictions = json.load(f)
        print(f"Загружено {len(all_predictions)} предсказаний.")
    except FileNotFoundError:
        print(f"Файл предсказаний {filename} не найден.")
        all_predictions = []
    except json.JSONDecodeError:
        print(f"Ошибка декодирования JSON в файле {filename}.")
        all_predictions = []

def save_known_users():
    try:
        with open(USERS_DATA_FILE, 'w', encoding='utf-8') as f:
            json.dump(known_users_data, f, ensure_ascii=False, indent=4)
        print(f"Данные пользователей сохранены в {USERS_DATA_FILE} ({len(known_users_data)} пользователей)")
    except Exception as e:
        print(f"Ошибка сохранения данных пользователей: {e}")

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
        print(f"Не удалось загрузить данные пользователей: {e}")
        known_users_data = {}

async def send_prediction_to_user(bot: telegram.Bot, user_info: dict, prediction: dict):
    user_id = user_info['id']
    mention_name = user_info.get('username', user_info['first_name']) 

    if user_info.get('username'):
        mention = f"@{user_info['username']}"
    else:
        mention = f"[{user_info['first_name']}](tg://user?id={user_id})"
    
    message_text = f"{mention}, ваше предсказание на сегодня: {prediction['text']}"

    try:
        await bot.send_message(
            chat_id=TARGET_CHANNEL_ID,
            text=message_text,
            parse_mode=ParseMode.MARKDOWN
        )
        print(f"Предсказание (ID: {prediction['id']}) отправлено для {mention} в канал {TARGET_CHANNEL_ID}")
        return True
    except telegram.error.TelegramError as e:
        print(f"Ошибка отправки предсказания для {mention} (ID: {user_id}) в канал {TARGET_CHANNEL_ID}: {e}")
        # Расширенная обработка ошибок
        error_text = str(e).lower()
        if any(err_keyword in error_text for err_keyword in ["user not found", "chat member not found", "bot was kicked", "user is deactivated", "chat not found", "group chat was deactivated"]):
            if user_id in known_users_data:
                print(f"Пользователь {mention_name} (ID: {user_id}) не найден/деактивирован/покинул канал или проблема с каналом. Удаляем из списка рассылки.")
                del known_users_data[user_id]
                save_known_users()
        return False

async def daily_prediction_job(context: CallbackContext):
    global predictions_sent_today_ids
    
    print(f"Запуск ежедневной рассылки предсказаний... Время: {datetime.datetime.now(pytz.timezone('Europe/Moscow'))}")
    predictions_sent_today_ids.clear()

    if not all_predictions:
        print("Нет доступных предсказаний.")
        if ADMIN_USER_ID:
             await context.bot.send_message(chat_id=ADMIN_USER_ID, text="Администратору: Список предсказаний пуст. Не могу начать рассылку.")
        return

    if not known_users_data:
        print("Нет известных пользователей для отправки предсказаний (никто не писал в канале, либо файл known_users.json пуст/недоступен).")
        return

    users_to_message = list(known_users_data.values())
    random.shuffle(users_to_message)
    
    available_predictions_for_today = [p for p in all_predictions if p['id'] not in predictions_sent_today_ids]
    random.shuffle(available_predictions_for_today)
    
    if not available_predictions_for_today:
        print("Все предсказания уже были использованы или их нет.")
        if ADMIN_USER_ID:
            await context.bot.send_message(chat_id=ADMIN_USER_ID, text="Кажется, на сегодня все предсказания уже были розданы или закончились!")
        return

    for user_info in users_to_message:
        if not available_predictions_for_today:
            print("Уникальные предсказания на сегодня закончились, не все пользователи их получили.")
            if ADMIN_USER_ID:
                await context.bot.send_message(chat_id=ADMIN_USER_ID, text="Уникальные предсказания на сегодня закончились!")
            break

        current_prediction = available_predictions_for_today.pop(0)
        success = await send_prediction_to_user(context.bot, user_info, current_prediction)
        
        if success:
            predictions_sent_today_ids.add(current_prediction['id'])
            print(f"Осталось {len(available_predictions_for_today)} уникальных предсказаний на сегодня.")
            await asyncio.sleep(30)
        else:
            print(f"Пропуск задержки для пользователя {user_info.get('username', user_info['first_name'])} из-за ошибки отправки.")
    
    print("Ежедневная рассылка завершена.")

async def store_user_from_channel_message(update: Update, context: CallbackContext):
    if not update.message or not update.message.from_user:
        return 

    user = update.message.from_user
    if user.is_bot:
        return

    if update.message.chat_id != TARGET_CHANNEL_ID:
        return

    # Обновляем данные, если пользователь уже есть, но изменились username/first_name, или добавляем нового
    if user.id not in known_users_data or \
       known_users_data[user.id].get('username') != user.username or \
       known_users_data[user.id].get('first_name') != user.first_name or \
       known_users_data[user.id].get('last_name') != user.last_name: # Добавил last_name для полноты
        
        known_users_data[user.id] = {
            "id": user.id,
            "first_name": user.first_name,
            "last_name": user.last_name,
            "username": user.username
        }
        save_known_users() 
        print(f"Пользователь {user.first_name} (ID: {user.id}, Username: @{user.username}) "
              f"обнаружен/обновлен в канале {TARGET_CHANNEL_ID}. "
              f"Всего пользователей: {len(known_users_data)}")

async def start_command(update: Update, context: CallbackContext):
    user = update.effective_user
    await update.message.reply_text(
        f"Привет, {user.first_name}! Я бот предсказаний для канала (ID: {TARGET_CHANNEL_ID}).\n"
        "Предсказания отправляются активным участникам этого канала ежедневно в {SCHEDULE_HOUR}:{SCHEDULE_MINUTE:02d} МСК.\n"
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

    load_known_users() # Обновим список перед показом, на всякий случай
    if not known_users_data:
        await update.message.reply_text("Список отслеживаемых пользователей пуст (никто еще не писал в целевом канале, либо файл не загружен).")
        return

    message = "Пользователи, для которых будут отправляться предсказания (обнаружены в канале):\n"
    for user_id, info in known_users_data.items():
        name = f"@{info['username']}" if info['username'] else info['first_name']
        message += f"- {name} (ID: {user_id})\n"
    
    max_len = 4096
    for i in range(0, len(message), max_len):
        await update.message.reply_text(message[i:i+max_len])

async def force_send_command(update: Update, context: CallbackContext):
    user = update.effective_user
    if not (ADMIN_USER_ID and user and user.id == ADMIN_USER_ID):
        await update.message.reply_text("Эта команда доступна только администратору бота.")
        return
    
    await update.message.reply_text("Принудительный запуск рассылки предсказаний...")
    # Перед принудительной отправкой, загрузим актуальный список пользователей
    load_known_users() 
    await daily_prediction_job(context) 

async def error_handler(update: object, context: CallbackContext) -> None:
    print(f"Ошибка при обработке обновления: {context.error}")
    if ADMIN_USER_ID:
        try:
            error_message = f"Произошла ошибка в боте:\nUpdate: {update}\nError: {context.error}"
            max_len = 4000 
            if len(error_message) > max_len:
                error_message = error_message[:max_len] + "\n... (сообщение обрезано)"
            await context.bot.send_message(chat_id=ADMIN_USER_ID, text=error_message)
        except Exception as e:
            print(f"Не удалось отправить сообщение об ошибке администратору: {e}")

def main():
    print("Загрузка данных...")
    load_predictions_from_file()
    load_known_users() # Загрузка пользователей при старте

    print("Создание экземпляра Application...")
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("list_users", list_users_command))
    application.add_handler(CommandHandler("force_send", force_send_command))

    application.add_handler(MessageHandler(
        filters.Chat(chat_id=TARGET_CHANNEL_ID) & filters.TEXT & (~filters.COMMAND) & (~filters.UpdateType.EDITED_MESSAGE),
        store_user_from_channel_message
    ))
    print(f"Настроен обработчик сообщений для канала ID: {TARGET_CHANNEL_ID}")
    
    application.add_error_handler(error_handler)

    job_queue = application.job_queue
    moscow_tz = pytz.timezone('Europe/Moscow')
    # Убедимся, что время для планировщика - это объект datetime.time
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
