r"""
filters.py
Содержит функции и константы для быстрой фильтрации лид‑сообщений.

Функции:
• extract_stems(entry)        – рекурсивно собирает все keywords из категорий/подкатегорий.
• is_similar(a, b, threshold) – проверяет частичное совпадение строк через RapidFuzz.
• contains_negative(text)     – детектирует слова‑триггеры «осторожно, спам, мошенник…»,
                                игнорируя «не спам» благодаря отрицанию (?<!не\s).
"""

from __future__ import annotations
from typing import Any, List
import re
from rapidfuzz import fuzz
import logging

# ---------- Negative context -----------------------------------------------
NEGATIVE_STEMS: List[str] = [
    "осторожн",
    "мошенник",
    "спам",
    "реклама",
    "мошенников",
    "предоплат",
]

_NEGATIVE_REGEX = re.compile(
    rf"(?<!\bне\s)({'|'.join(NEGATIVE_STEMS)})",
    flags=re.IGNORECASE
)

def contains_negative(text: str) -> bool:
    """True, если сообщение содержит нежелательный контекст."""
    return bool(_NEGATIVE_REGEX.search(text.lower()))


# ---------- Fuzzy similarity ------------------------------------------------
def is_similar(a: str, b: str, threshold: int = 70) -> bool:
    """
    Быстрая проверка частичного совпадения строк с помощью RapidFuzz.
    threshold – минимальный процент совпадения (0–100).
    """
    return fuzz.partial_ratio(a.lower(), b.lower()) >= threshold


# ---------- Keyword stems extraction ---------------------------------------
def extract_stems(entry: Any) -> List[str]:
    """
    Рекурсивно собирает все строки‑ключевые слова из
    словаря категорий/подкатегорий:

    {
        "keywords": [...],
        "subcategories": {
            "подкат": { "keywords": [...] }
        }
    }
    """
    stems: List[str] = []
    if isinstance(entry, dict):
        if "keywords" in entry and isinstance(entry["keywords"], list):
            stems.extend(entry["keywords"])
        # рекурсивно вглубь всех ключей, кроме keywords
        for key, val in entry.items():
            if key != "keywords":
                stems.extend(extract_stems(val))
    elif isinstance(entry, list):
        for item in entry:
            stems.extend(extract_stems(item))
    elif isinstance(entry, str):
        stems.append(entry)
    return stems