LOCATION_ALIAS = {
    "анталия": "Анталия",
    "анталья": "Анталия",
    "кемер": "Кемер",
    "стамбул": "Стамбул",
    "белдиби": "Бельдиби",
    "бельдиби": "Бельдиби",
    "гейнюк": "Гёйнюк",
    "гёйнюк": "Гёйнюк",
    "манавгат": "Манавгат",
    "чамьюва": "Чамьюва",
    "турция": "Турция",
    "сиде": "Сиде"
}
import os
import json
import logging
from telethon import TelegramClient
from collections import Counter
from subscription import subscriptions, save_subscriptions

from dotenv import load_dotenv
dotenv_path = os.path.join(os.path.dirname(__file__), '.env')
load_dotenv(dotenv_path)

logger = logging.getLogger("bot_logger")
logger.setLevel(logging.INFO)

from logging.handlers import RotatingFileHandler

# File handler with rotation: 5 MB per file, keep 5 backups
_log_formatter = logging.Formatter("%(asctime)s %(levelname)s: %(message)s", "%m-%d %H:%M")
_file_handler = RotatingFileHandler("bot.log", maxBytes=5_000_000, backupCount=5, encoding="utf-8")
_file_handler.setFormatter(_log_formatter)
logger.addHandler(_file_handler)

# If you want verbose debug logging, export BOT_DEBUG=1 before running the bot
if os.getenv("BOT_DEBUG") == "1":
    logger.setLevel(logging.DEBUG)

metrics = Counter()

# Safe environment variable loading and validation
api_id_str = os.getenv("API_ID")
if not api_id_str:
    raise RuntimeError("API_ID is not set in .env")
api_id = int(api_id_str)

api_hash = os.getenv("API_HASH")
if not api_hash:
    raise RuntimeError("API_HASH is not set in .env")

bot_token = os.getenv("LEADBOT_TOKEN", os.getenv("BOT_TOKEN"))
if not bot_token:
    raise RuntimeError("LEADBOT_TOKEN is not set in .env")

admin_id_str = os.getenv("ADMIN_ID", "123456789")
if not admin_id_str.isdigit():
    raise RuntimeError("ADMIN_ID must be an integer")
ADMIN_ID = int(admin_id_str)

# categories — словарь из categories.json
with open("categories.json", encoding="utf-8") as f:
    categories = json.load(f)

bot_client = TelegramClient("bot", api_id, api_hash)
