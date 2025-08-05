import requests, socket
try:
    ip = requests.get("https://api.ipify.org", timeout=2).text
except Exception:
    ip = socket.gethostbyname(socket.gethostname())
print(f"[BOOT] Running on host IP: {ip}")
import os  # <‑‑ needed before using os.getenv below
VERBOSE_DEBUG = os.getenv("BOT_DEBUG") == "1"

# helper for Istanbul time
from datetime import datetime, timedelta, timezone

def now_istanbul():
    return datetime.now(timezone.utc) + timedelta(hours=3)
# Confidence threshold: below this value leads are flagged for review
CONF_THRESHOLD = 0.7
import json
import asyncio
import logging
from collections import Counter, deque
import atexit
from functools import lru_cache
import re
# Regex to extract Russian/English words for stemming
WORD_RE = re.compile(r"[а-яa-zё]+", re.IGNORECASE | re.UNICODE)

import snowballstemmer
_ru_stemmer = snowballstemmer.stemmer('russian')

def _stem(word: str) -> str:
    """Return lowercase snowball stem for Russian word."""
    return _ru_stemmer.stemWord(word.lower())

def _stem_in_text(needle: str, stems_set: set[str]) -> bool:
    """True if stem(needle) присутствует в тексте (по стемам)."""
    return _stem(needle) in stems_set

import keep_alive  # стартует Flask-сервер для keep-alive
from ai_utils import classify_text_with_ai, _classify_cache, apply_overrides

import openai
from openai import OpenAI


from dotenv import load_dotenv
from telethon import TelegramClient, events
from telethon import Button
from filters import extract_stems, is_similar, contains_negative

from config import ADMIN_ID, categories, metrics, logger, bot_client, subscriptions, save_subscriptions

# Persist metrics to JSON on shutdown
def dump_metrics():
    with open("metrics.json", "w", encoding="utf-8") as mf:
        json.dump(metrics, mf, ensure_ascii=False, indent=2)

atexit.register(dump_metrics)

 # --- Deduplication with TTL ---
MAX_SEEN_IDS = 50000
# Keep track of recent message IDs for deduplication with TTL
seen_queue = deque()
seen_set = set()

# --- Periodic cleaner --------------------------------------------------------
async def cleaner_task():
    """Раз в сутки очищает seen_* и _classify_cache, чтобы не росла память."""
    while True:
        await asyncio.sleep(24 * 60 * 60)  # 24h
        seen_queue.clear()
        seen_set.clear()
        _classify_cache.clear()
        logger.info("🧹 Caches cleared (seen_queue, seen_set, classify_cache)")



# Load environment variables
load_dotenv()
openai.api_key = os.getenv("OPENAI_API_KEY")
if not openai.api_key:
    raise RuntimeError("OPENAI_API_KEY is not set in .env")
api_id = int(os.getenv("API_ID"))
api_hash = os.getenv("API_HASH")
bot_token = os.getenv("LEADBOT_TOKEN", os.getenv("BOT_TOKEN"))
if not bot_token:
    raise RuntimeError("LEADBOT_TOKEN is not set in .env")

# Telegram user ID to receive manual payment proofs (replace with your ID)
# ADMIN_ID moved to config.py

# Initialize OpenAI client after API key is loaded
client_ai = OpenAI(api_key=openai.api_key)

from config import LOCATION_ALIAS
# List of canonical display names
CANONICAL_LOCATIONS = sorted(set(LOCATION_ALIAS.values()))



# Импортируем функцию отправки лидов из delivery.py
from delivery import send_lead_to_users

session_name = "bot_parser"
client = TelegramClient(session_name, api_id, api_hash, connection_retries=1)

# Bot client for UI and commands
# bot_client moved to config.py
import ui


@client.on(events.NewMessage)
async def handler(event):
    metrics['received'] += 1
    # Deduplication with TTL: skip if already seen
    if event.id in seen_set:
        metrics['deduplicated'] += 1
        return
    # Add new ID and enforce max size
    seen_queue.append(event.id)
    seen_set.add(event.id)
    if len(seen_queue) > MAX_SEEN_IDS:
        old_id = seen_queue.popleft()
        seen_set.remove(old_id)
    # Only groups and channels
    if not (event.is_group or event.is_channel):
        return
    chat_id = event.chat_id
    # Ignore own messages
    global SELF_ID
    if event.sender_id == SELF_ID:
        return
    text = event.raw_text or ""
    # Remove hashtags to avoid false matches
    clean_text = re.sub(r'#\w+', '', text)
    # Lowercase text for matching
    lower_text = clean_text.lower()
    # Pre‑compute set of stems in the message for fast membership test
    stems_in_text = {_stem(tok) for tok in WORD_RE.findall(lower_text)}

    dialog = await event.get_chat()
    group_name = getattr(dialog, 'title', 'Без названия')

    sender_entity = await event.get_sender()
    if sender_entity:
        sender_id = sender_entity.id
        sender_name = getattr(sender_entity, 'first_name', None) or getattr(sender_entity, 'username', 'Неизвестный отправитель')
        sender_username = getattr(sender_entity, 'username', None)
    else:
        # Fallback for channels without a user sender
        sender_id = event.chat_id
        sender_name = group_name
        sender_username = None



    # Determine region by group title first, then by message text
    found_alias = None
    title_lower = group_name.lower()
    for alias in LOCATION_ALIAS:
        if alias in title_lower or is_similar(alias, title_lower, threshold=60):
            found_alias = alias
            break
    if not found_alias:
        for alias in LOCATION_ALIAS:
            if alias in lower_text or is_similar(alias, lower_text, threshold=60):
                found_alias = alias
                break
    if not found_alias:
        metrics['no_region'] += 1
        return
    region = LOCATION_ALIAS[found_alias]
    metrics['region_detected'] += 1

    # Heuristic category detection: match any category stem in text (support nested)
    category_heuristic = None
    for cat, stems in categories.items():
        for stem in extract_stems(stems):
            if _stem_in_text(stem.lower(), stems_in_text):
                category_heuristic = cat
                break
        if category_heuristic:
            break
    if category_heuristic:
        metrics['category_heuristic_detected'] += 1
    else:
        metrics['category_not_detected'] += 1
    if found_alias and category_heuristic:
        metrics['coverage_ok'] += 1

    # User-specific pre-filter: check if any subscriber needs this region and category
    # Find subscribers for this region
    subscribers_for_region = [
        prefs for prefs in subscriptions.values()
        if region in prefs.get("locations", [])
    ]
    if not subscribers_for_region:
        metrics['no_subscribers_for_region'] += 1
        return
    # Build stems set from all these subscribers' categories и их подкатегорий
    stems_for_users = set()
    stem_to_category = {}  # new: map stem → category we added it from
    for prefs in subscribers_for_region:
        # категории
        for cat in prefs.get("categories", []):
            for stem in extract_stems(categories.get(cat, {})):
                stem_key = _stem(stem)
                stems_for_users.add(stem_key)
                stem_to_category.setdefault(stem_key, cat)
        # подкатегории
        for cat, sub_list in prefs.get("subcats", {}).items():
            for sub in sub_list:
                sub_entry = categories.get(cat, {}).get("subcategories", {}).get(sub, {})
                for stem in extract_stems(sub_entry):
                    stem_key = _stem(stem)
                    stems_for_users.add(stem_key)
                    stem_to_category.setdefault(stem_key, f"{cat}/{sub}")

    user_stems = {s.lower() for s in stems_for_users}
    # Detect and log first matched stem
    matched_stem = next((s for s in user_stems if s in stems_in_text), None)
    if not matched_stem:
        metrics['no_category_match'] += 1
        return
    matched_cat = stem_to_category.get(matched_stem, "?")
    logger.info(f"Keyword match: '{matched_stem}' → category '{matched_cat}'")
    # AI classification with caching
    try:
        # Only use [category_heuristic] if present, else full list
        cats_to_use = [category_heuristic] if category_heuristic else list(categories.keys())
        cla = await asyncio.to_thread(
            classify_text_with_ai,
            text,
            cats_to_use,
            CANONICAL_LOCATIONS,
            client_ai
        )
    except Exception as e:
        logger.error(f"[AI ERROR] {e}")
        return

    # Override AI classification with heuristics and post-hoc rules
    if isinstance(cla, dict):
        cla["region"] = region
        cla = apply_overrides(cla, lower_text, category_heuristic)

    # Drop if no response or not relevant, with debug explanation
    if not cla or not cla.get("relevant", False):
        metrics['ai_dropped'] += 1
        if VERBOSE_DEBUG:
            logger.debug(f"AI dropped message: relevant={cla.get('relevant') if cla else None}")
            relevant = cla.get("relevant") if isinstance(cla, dict) else None
            explanation = cla.get("explanation") if isinstance(cla, dict) else None
            logger.debug(f"AI dropped message. relevant={relevant}, explanation={explanation}, full={cla}")
        # Append to ai_rejected.log with classification details and concise timestamp
        try:
            ts = now_istanbul().strftime("%m-%d %H:%M")
            with open("ai_rejected.log", "a", encoding="utf-8") as rf:
                rf.write(
                    f"{ts} | {chat_id} ({group_name}) | {text} | "
                    f"relevant:{cla.get('relevant')}, "
                    f"category:{cla.get('category')}, "
                    f"region:{cla.get('region')}, "
                    f"explanation:{cla.get('explanation')}, "
                    f"confidence:{cla.get('confidence')}\n"
                )
        except Exception as e:
            logger.error(f"Failed to write to ai_rejected.log: {e}")
        return

    # Handle low-confidence yet relevant cases
    confidence = cla.get("confidence", 0.0)
    if confidence < CONF_THRESHOLD:
        metrics['low_confidence'] += 1
        logger.info(f"Low confidence ({confidence}) for message, flagging for review")
        # Append to ai_low_confidence.log for later analysis
        try:
            ts = now_istanbul().strftime("%m-%d %H:%M")
            with open("ai_low_confidence.log", "a", encoding="utf-8") as lf:
                lf.write(
                    f"{ts} | {chat_id} ({group_name}) | {text} | "
                    f"relevant:{cla.get('relevant')}, "
                    f"category:{cla.get('category')}, "
                    f"region:{cla.get('region')}, "
                    f"explanation:{cla.get('explanation')}, "
                    f"confidence:{confidence}\n"
                )
        except Exception as e:
            logger.error(f"Failed to write to ai_low_confidence.log: {e}")
        return

    # Skip messages where the AI could not assign a category
    detected_cat = cla.get("category") if isinstance(cla, dict) else None
    if not detected_cat:
        metrics['ai_no_category'] += 1
        return

    # Log relevant classification
    logger.info(
    f"{chat_id} ({group_name}) | {text} | "
    f"relevant:{cla.get('relevant')}, "
    f"category:{cla.get('category')}, "
    f"region:{cla.get('region')}, "
    f"explanation:{cla.get('explanation')}, "
    f"confidence:{cla.get('confidence')}"
)
    # Optionally validate region/category but keep region from heuristics

    # Build message link for supergroups
    if str(chat_id).startswith("-100"):
        short = str(chat_id)[4:]
        link = f"https://t.me/c/{short}/{event.id}"
    else:
        link = ""
    await send_lead_to_users(
        chat_id,
        group_name,
        getattr(dialog, 'username', None),
        sender_name,
        sender_id,
        sender_username,
        text,
        link,
        region,
        detected_category=detected_cat
    )

SELF_ID = None

async def main():
    global SELF_ID
    # Start parser (user) session
    await client.start()
    # Start bot session
    await bot_client.start(bot_token=bot_token)
    me = await bot_client.get_me()
    SELF_ID = me.id
    logger.info(f"✅ Bot logged in as @{me.username} (id={me.id})")
    logger.info("🚀 Both clients running")
    if VERBOSE_DEBUG:
        logger.debug("Handler invoked, deduplication in place")
    await asyncio.gather(
        client.run_until_disconnected(),
        bot_client.run_until_disconnected(),
        cleaner_task(),
    )

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("📴 Bot stopped by user")
        # Graceful exit on Ctrl+C
        logger.info("=== Metrics Summary ===")
        for key, count in metrics.items():
            logger.info(f"{key}: {count}")