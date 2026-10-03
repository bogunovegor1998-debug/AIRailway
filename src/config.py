"""
Конфигурация приложения — все настройки из .env.
"""
from dotenv import load_dotenv
import os

load_dotenv()

# === Telegram ===
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

# === PostgreSQL ===
DATABASE_URL = os.environ["DATABASE_URL"]

# === DeepSeek ===
DEEPSEEK_API_KEY = os.environ["DEEPSEEK_API_KEY"]
DEEPSEEK_BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")

# === Параметры прохода (v2.0: раздельные потоки) ===
TURNSTILE_PASS_SECONDS = int(os.environ.get("TURNSTILE_PASS_SECONDS", 6))   # турникеты (прибывающие)
FRAME_PASS_SECONDS = int(os.environ.get("FRAME_PASS_SECONDS", 10))           # рамки (отправляющиеся)

# === Всего оборудования ===
FRAMES_TOTAL = int(os.environ.get("FRAMES_TOTAL", 17))
TURNSTILES_TOTAL = int(os.environ.get("TURNSTILES_TOTAL", 17))

# === Пороги зон нагрузки (минуты) ===
ZONE_GREEN_MAX = int(os.environ.get("ZONE_GREEN_MAX", 3))
ZONE_YELLOW_MAX = int(os.environ.get("ZONE_YELLOW_MAX", 5))

# === Порог пассажиров на поезд для причины "большое количество пассажиров" ===
PASSENGERS_PER_TRAIN_THRESHOLD = int(os.environ.get("PASSENGERS_PER_TRAIN_THRESHOLD", 500))

# === Расписание дайджеста ===
DAILY_DIGEST_HOUR = int(os.environ.get("DAILY_DIGEST_HOUR", 7))
DAILY_DIGEST_MINUTE = int(os.environ.get("DAILY_DIGEST_MINUTE", 0))

# === Вокзал ===
STATION_NAME = os.environ.get("STATION_NAME", "Ленинградский вокзал")

# Координаты Ленинградского вокзала (для погоды)
STATION_LAT = float(os.environ.get("STATION_LAT", 55.7766))
STATION_LON = float(os.environ.get("STATION_LON", 37.6553))

# === Погода ===
WEATHER_CITY = os.environ.get("WEATHER_CITY", "Moscow")

# === Пробки (баллы по дням недели, 0=пн) ===
TRAFFIC_BY_WEEKDAY = {
    0: 8,   # понедельник
    1: 7,   # вторник
    2: 6,   # среда
    3: 7,   # четверг
    4: 9,   # пятница
    5: 6,   # суббота
    6: 4,   # воскресенье
}

# === Пароль доступа к боту (пустая строка = без пароля) ===
BOT_PASSWORD = os.environ.get("BOT_PASSWORD", "").strip()

# === Данные ===
DATA_DIR = os.environ.get("DATA_DIR", "data")
