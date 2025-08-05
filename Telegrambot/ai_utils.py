import re
import json
import os
from datetime import datetime, timezone, timedelta
import time
import threading
from tenacity import retry, wait_exponential, stop_after_attempt, retry_if_exception_type
try:
    from openai import OpenAI, RateLimitError, APIError, Timeout
except ImportError:
    import openai
    from openai import OpenAI  # клиент есть
    # shim: если в версии openai нет конкретных классов, подменяем Exception
    RateLimitError = getattr(openai, "RateLimitError", Exception)
    APIError = getattr(openai, "APIError", Exception)
    Timeout = getattr(openai, "Timeout", Exception)

DEBUG_PROMPT_TRACE = False

import snowballstemmer
_ru_stemmer = snowballstemmer.stemmer('russian')

def _stem(word: str) -> str:
    """Return lowercase snowball stem for Russian word."""
    return _ru_stemmer.stemWord(word.lower())

CONF_THRESHOLD = 0.7  # confidence threshold for auto-accepting leads

# Простой ручной кэш, потому что списки (list) не хешируемы для lru_cache
_classify_cache = {}
_CLASSIFY_CACHE_MAXSIZE = 5000

# --- Rate‑limit settings ---
_RATE_LIMIT_RPS = float(os.getenv("OPENAI_RPS", "3"))  # макс. запросов в секунду
_MIN_INTERVAL = 1.0 / _RATE_LIMIT_RPS
_last_call_ts = 0.0
_rate_lock = threading.Lock()


# Helper for rate-limit
def _apply_rate_limit():
    """Блокирует поток, чтобы выдержать заданный RPS."""
    global _last_call_ts
    with _rate_lock:
        now = time.time()
        wait_sec = _MIN_INTERVAL - (now - _last_call_ts)
        if wait_sec > 0:
            time.sleep(wait_sec)
        _last_call_ts = time.time()


# Обёртка с retry
@retry(
    wait=wait_exponential(min=1, max=60, multiplier=2),
    stop=stop_after_attempt(6),
    retry=retry_if_exception_type((RateLimitError, APIError, Timeout))
)
def _chat_completion_with_retry(client: OpenAI, messages: list, model: str = "gpt-4.1-nano", temperature: float = 0):
    """Вызов chat.completions с rate‑limit и автоматическим back‑off."""
    _apply_rate_limit()
    return client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=temperature,
    )

# Инициализация клиента OpenAI (лениво, если не передан)
def get_openai_client():
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set in .env")
    return OpenAI(api_key=api_key)

# Вспомогательные детекторы
_contact_regex = re.compile(r"(@[\w_]+|t\.me/[\w_\-]+|https?://t\.me/[\w_\-]+|\+?[\d\-\s\(\)]{7,}|whatsapp)", flags=re.IGNORECASE)
_seller_patterns = [
    r"\bпредлагаем\b", r"\bзабронируйте\b", r"\bузнайте стоимость\b", r"\bнаши услуги\b",
    r"\bVIP\b", r"\bпревратите\b", r"\bскидк\b", r"\bдешевле\b", r"\bпродаем\b",
    r"\bзабронируйте сейчас\b", r"\bзарезервируйте\b", r"\bспецпредложение\b", r"\bкупить сейчас\b", r"\bскидка на страховку\b"
]

def contains_contact(s: str) -> bool:
    return bool(_contact_regex.search(s))


def contains_seller_speech_act(s: str) -> bool:
    for pat in _seller_patterns:
        if re.search(pat, s, flags=re.IGNORECASE):
            return True
    return False

def _sanitize_result(result: dict) -> dict:
    # Ensure keys exist with defaults
    relevant = result.get("relevant", False)
    category = result.get("category")
    region = result.get("region")
    explanation = result.get("explanation", "")
    confidence = result.get("confidence", 0.0)
    # Coerce confidence to float and clamp between 0 and 1
    try:
        confidence = float(confidence)
    except Exception:
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))

    # Truncate explanation to 70 chars with ellipsis if needed
    if len(explanation) > 70:
        explanation = explanation[:67] + "..."

    # Determine accepted and borderline flags
    accepted = relevant is True and confidence >= CONF_THRESHOLD
    borderline = False
    if relevant is True and confidence < CONF_THRESHOLD:
        borderline = True

    # Update result dict
    result["relevant"] = relevant
    result["category"] = category
    result["region"] = region
    result["explanation"] = explanation
    result["confidence"] = confidence
    result["accepted"] = accepted
    if borderline:
        result["borderline"] = True
    elif "borderline" in result:
        del result["borderline"]

    return result

def calibrate_confidence(raw_confidence: float) -> float:
    """Piecewise calibration of confidence based on observed buckets."""
    if raw_confidence >= 0.9:
        calibrated = 0.92
    elif 0.8 <= raw_confidence < 0.9:
        calibrated = 0.85
    elif 0.6 <= raw_confidence < 0.8:
        calibrated = 0.7
    elif 0.5 <= raw_confidence < 0.6:
        calibrated = 0.55
    else:
        calibrated = raw_confidence
    return max(0.0, min(1.0, calibrated))

def _select_subset(options: list[str], text: str, limit: int = 12) -> list[str]:
    """
    Возвращает до `limit` элементов из options:
    1) сначала те, чья подстрока встречается в `text` (без учёта регистра);
    2) затем первые из списка до заполнения лимита.
    """
    text_lc = text.lower()
    hits = [opt for opt in options if opt.lower() in text_lc]
    # Сохраняем порядок оригинального списка
    unique_hits = []
    seen = set()
    for opt in hits + options:
        if opt not in seen:
            unique_hits.append(opt)
            seen.add(opt)
        if len(unique_hits) == limit:
            break
    return unique_hits

def classify_text_with_ai(text: str,
                          categories: list,
                          locations: list,
                          client_ai=None) -> dict:
    """
    Возвращает dict с keys: relevant, category, region, explanation, confidence.
    Может добавлять override_reason.
    """
    if client_ai is None:
        client_ai = get_openai_client()

    # Сформировать списки для промпта
    # Сокращаем списки — максимум 12 шт., сначала совпадения по тексту
    cat_subset = _select_subset(categories, text, limit=12)
    loc_subset = _select_subset(locations, text, limit=12)
    category_list = ', '.join(f'"{cat}"' for cat in cat_subset)
    location_list = ', '.join(f'"{loc}"' for loc in loc_subset)

    # Ключ для кэша (преобразуем списки в кортежи)
    key = (text, tuple(cat_subset), tuple(loc_subset))
    if key in _classify_cache:
        return _classify_cache[key].copy()

    system_prompt = (
        """
Ты — профессиональный классификатор запросов и лидов для Telegram. Цель: максимально точно определить, является ли входящее сообщение запросом услуги (лидом) или рекламным/продающим. Приоритетное правило: если сообщение содержит контактные данные (телефон, @username, ссылки вида t.me/...) и одновременно написано от лица продавца/организатора (например: предлагаем, забронируйте, узнайте стоимость, наши услуги, VIP, скидка, дешевле, продаем, забронируйте сейчас), то это реклама: relevant=false. Речь заинтересованного пользователя (вопрос, просьба): нужен, хочу, сколько стоит, подскажите и пр. — это лид. Отвечай только одним валидным JSON-объектом с ключами: relevant (boolean), category (одно из: """ + category_list + """ или null), region (одно из: """ + location_list + """ или null), explanation (короткая строка до 70 символов), confidence (число от 0.0 до 1.0). Модель должна варьировать confidence в зависимости от неоднозначности (напр., сомнительные сигналов: 0.4-0.7; явные лиды/реклама: около 0.9). Никаких списков или лишних ключей.
"""
    )

    user_prompt = (
        f"""
Примеры:
Сообщение: 'Нужен трансфер из Анталии в Кемер завтра' Ответ: {{"relevant": true, "category": "трансфер", "region": "Анталия", "explanation": "Запрос трансфера"}}
Сообщение: 'Сколько стоит экскурсия в Памуккале?' Ответ: {{"relevant": true, "category": "экскурсии", "region": "Памуккале", "explanation": "Уточнение цены"}}
Сообщение: 'Не интересует аренда скутеров' Ответ: {{"relevant": false, "category": "аренда", "region": null, "explanation": "Отказ от услуги"}}
Сообщение: 'Экскурсия ТУРЕЦКИЙ ДИСНЕЙЛЕНД, дешевле чем в кассе. Узнать стоимость и забронировать билеты. По вопросам: @vip' Ответ: {{"relevant": false, "category": null, "region": null, "explanation": "Реклама/продажа"}}
Сообщение: 'Может возьмём экскурсию и трансфер вместе позже' Ответ: {{"relevant": true, "category": "экскурсии", "region": null, "explanation": "Неоднозначный интерес"}}
Сообщение: 'Хочу узнать, но ещё не уверен' Ответ: {{"relevant": true, "category": null, "region": null, "explanation": "Запрос с неуверенностью"}}
Сообщение: 'Трансфер из аэропорта Анталии, звоните +7 999 1234567, забронируйте сейчас!' Ответ: {{"relevant": false, "category": null, "region": null, "explanation": "Реклама/продажа"}}
Сообщение: 'Может возьму экскурсию позже, пока присматриваюсь' Ответ: {{"relevant": false, "category": null, "region": null, "explanation": "Неуверенный интерес"}}
Анализируй это сообщение:
\"\"\"{text}\"\"\"
"""
    )

    # Store raw prompt for debug/tracing
    raw_prompt = {
        "system": system_prompt,
        "user": user_prompt
    }

    try:
        resp = _chat_completion_with_retry(
            client_ai,
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            model="gpt-4.1-nano",
            temperature=0,
        )
    except (RateLimitError, APIError, Timeout, Exception) as e:
        return {
            "relevant": False,
            "category": None,
            "region": None,
            "explanation": f"OpenAI error: {e}",
            "confidence": 0.0,
            "accepted": False,
        }
    content = resp.choices[0].message.content.strip()
    raw_model_output = content
    try:
        result = json.loads(content)
    except Exception as e:
        return {
            "relevant": False,
            "category": None,
            "region": None,
            "explanation": f"Ошибка парсинга ответа ИИ: {e}",
            "confidence": 0.0,
            "raw": content,
            "accepted": False,
            "raw_prompt": raw_prompt,
            "raw_model_output": raw_model_output,
        }
    # Sanitize, calibrate, and sanitize again (single clear flow)
    result = _sanitize_result(result)
    raw_conf = result.get("confidence", 0.0)
    result["confidence"] = calibrate_confidence(raw_conf)
    result = _sanitize_result(result)

    # Post-override: self-promo detection only
    text_lower = text.lower()
    if result.get("relevant") and contains_contact(text_lower) and contains_seller_speech_act(text_lower):
        result["relevant"] = False
        result["explanation"] = "Self-promo с контактами"
        result["confidence"] = min(result.get("confidence", 1.0), 0.4)
        result["override_reason"] = "self_promo"
        result["category"] = None
        result = _sanitize_result(result)

    # Attach prompt and model output for trace/debug only if DEBUG_PROMPT_TRACE
    if DEBUG_PROMPT_TRACE:
        result["raw_prompt"] = raw_prompt
        result["raw_model_output"] = raw_model_output

    # Кешируем результат (копию) и ограничиваем размер
    if len(_classify_cache) >= _CLASSIFY_CACHE_MAXSIZE:
        _classify_cache.pop(next(iter(_classify_cache)))
    _classify_cache[key] = result.copy()

    return result

# --- apply_overrides and helpers moved from Botparsing.py ---
def apply_overrides(cla, lower_text, category_heuristic):
    """
    Post‑hoc override logic for classification adjustment (heuristics only).
    """
    if category_heuristic:
        cla["category"] = category_heuristic

    lower = lower_text

    sale_words = ["продается", "продаю", "продам", "продажа", "куплю", "инвестировать", "вложение", "вложиться"]
    rent_supply = ["сдам", "сдаю", "сдаётся", "сдается", "арендодатель"]
    rent_demand = ["сниму", "нужна квартира", "ищу аренду", "арендатор", "хочу снять"]
    transfer_indicators = [
        "из аэропорта", "в аэропорт", "встреча", "шаттл", "такси", "подвоз", "трансфер", "поездка", "водитель", "подвезти"
    ]

    # Продажа/инвестиции (недвижимость)

    # Аренда: supply vs demand

    # Трансферные индикаторы
    if any(ind in lower for ind in transfer_indicators):
        cla["category"] = "трансфер"

    # Реклама страховки: salesy без вопроса
    if "страховка" in lower:
        question_indicators = ["сколько стоит", "нужна", "хочу", "подскажите", "как получить", "какая", "можно ли"]
        salesy = any(w in lower for w in ["купить", "оформить", "предлагаем", "продаем", "продаю", "выгодно"])
        if salesy and not any(q in lower for q in question_indicators):
            cla["relevant"] = False
            cla["explanation"] = "Реклама страховки"

    # Калибровка confidence: если эвристика совпадает и низкая уверенность, чуть поднимаем
    conf = cla.get("confidence", 0.0)
    if category_heuristic and cla.get("category") == category_heuristic and conf < 0.8:
        cla["confidence"] = min(0.85, conf + 0.1)

    return cla

def _stem_in_text(needle: str, stems_set: set[str]) -> bool:
    """True if stem(needle) присутствует в тексте (по стемам)."""
    return _stem(needle) in stems_set


# --- Exports ---------------------------------------------------------------
__all__ = [
    "classify_text_with_ai",
    "apply_overrides",
    "_classify_cache",
]