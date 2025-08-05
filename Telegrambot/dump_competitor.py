import os
import json
import asyncio
from telethon import TelegramClient
from dotenv import load_dotenv
import re

CANONICAL_LOCATIONS = ["Анталия", "Бельдиби", "Гёйнюк", "Кемер", "Манавгат", "Сиде", "Стамбул", "Турция", "Чамьюва"]

load_dotenv()  # читает .env рядом

api_id = int(os.getenv("API_ID"))
api_hash = os.getenv("API_HASH")

# Используем сессию пользователя с ID 5524768195 для парсинга
user_session_name = 'user_5524768195'
client = TelegramClient(user_session_name, api_id, api_hash)

# Целевая группа/канал, можно заменить на username или ID
TARGET = "@leadder_bot"  # пример, замените на нужный

def anonymize(text: str) -> str:
    # Drop initial group header if there's a blank line separator (first paragraph)
    if '\n\n' in text:
        text = text.split('\n\n', 1)[1]
    # Now normalize by lines to remove leftover header-like fragments and "Возможный регион"
    lines = text.splitlines()
    filtered_lines = []
    for line in lines:
        orig = line
        line = line.strip()
        if not line:
            continue
        # remove title/group header lines: containing 'Чат' or having pipe separators (e.g. "Турция | Жилье | Аланья")
        if re.search(r"\bЧат\b", line, flags=re.IGNORECASE) or ('|' in line and len(line.split('|')) >= 2):
            continue
        # skip lines that look like ALL CAPS navigation or ambiguous short titles with location names only
        if re.fullmatch(r'[\w\s,\-\\/❤️🇹🇷]+', line) and line == line.upper() and len(line) < 100:
            continue
        # drop any line that mentions "Возможный регион" (and do not include following hashtags)
        if re.search(r'возможный\s+регион', line, flags=re.IGNORECASE):
            continue
        filtered_lines.append(orig)
    text = "\n".join(filtered_lines)
    # Remove leading standalone canonical location followed by punctuation (e.g. "Стамбул.")
    text = re.sub(rf"^({'|'.join([re.escape(loc) for loc in CANONICAL_LOCATIONS])})[\.:]\s*", "", text, flags=re.IGNORECASE)
    # удалить хэштеги (#слово -> слово)
    text = re.sub(r"#", "", text)
    # удалить username, ссылки и номера
    text = re.sub(r'@\w+', '<user>', text)
    text = re.sub(r'https?://\S+', '<link>', text)
    text = re.sub(r'\+?\d[\d \-\(\)]{5,}\d', '<phone>', text)
    # сжать пробелы включая переносы строк в одиночные пробелы
    text = re.sub(r"\s+", " ", text).strip()
    # удалить лишние category-like токены в конце
    text = re.sub(r"\b\w+_\w+\b$", "", text).strip()
    return text

async def dump():
    await client.start()
    me = await client.get_me()
    print(f"Logged in as user: {me.id} ({me.username or me.first_name})")
    entity = await client.get_entity(TARGET)
    output_path = "competitor_leads1.jsonl"
    with open(output_path, "w", encoding="utf-8") as f:
        async for message in client.iter_messages(entity, limit=None):
            text = message.message or ""
            cleaned = anonymize(text)
            if not cleaned:
                continue
            item = {
                "text": cleaned,
                "timestamp": message.date.isoformat(),
            }
            f.write(json.dumps(item, ensure_ascii=False) + "\n")
    print(f"Saved to {output_path}")

if __name__ == "__main__":
    asyncio.run(dump())