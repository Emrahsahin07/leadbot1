import pytest
from ai_utils import classify_text_with_ai
import re

CATS = ["экскурсии", "трансфер", "аренда", "недвижимость"]
LOCS = ["Анталия", "Кемер", "Памуккале"]

@pytest.mark.parametrize("msg,expect_rel,expect_cat", [
    ("Нужен трансфер из Анталии завтра", True, "трансфер"),
    ("Сколько стоит экскурсия в Памуккале?", True, "экскурсии"),
    ("Не интересует аренда скутеров", False, "аренда"),
])
def test_basic(msg, expect_rel, expect_cat):
    res = classify_text_with_ai(msg, CATS, LOCS)
    assert res["relevant"] == expect_rel
    assert res["category"] == expect_cat

def test_ad_detect():
    msg = "Трансфер Анталия—Кемер, звоните +90 555 123, скидка!"
    res = classify_text_with_ai(msg, CATS, LOCS)
    assert res["relevant"] is False
    assert res["confidence"] <= 0.4

def test_region_detection():
    msg = "Ищу экскурсию в Кемер на завтра"
    res = classify_text_with_ai(msg, CATS, LOCS)
    assert res["relevant"] is True
    assert res["category"] == "экскурсии"
    assert res["region"] == "Кемер"

def test_borderline_confidence():
    msg = "Может возьмём экскурсию позже, пока присматриваюсь"
    res = classify_text_with_ai(msg, CATS, LOCS)
    # Ожидаем, что confidence в промежутке 0.3–0.7
    assert 0.3 <= res["confidence"] <= 0.7
    assert res["category"] == "экскурсии"

def test_property_lead():
    """Пользователь ищет жильё — должен быть лид по категории 'недвижимость'."""
    msg = "Сниму квартиру в Анталии на год, бюджет до 800$"
    res = classify_text_with_ai(msg, CATS, LOCS)
    assert res["relevant"] is True
    assert res["category"] == "недвижимость"
    assert res["region"] == "Анталия"
    assert res["confidence"] >= 0.7

def test_property_ad():
    """Продавец рекламирует жильё с контактом — должно быть отклонено как реклама."""
    msg = "Продаю квартиры в Кемере у моря, цены лучшие! Пишите @realty_agent"
    res = classify_text_with_ai(msg, CATS, LOCS)
    assert res["relevant"] is False
    assert res["category"] is None  # реклама → нет категории
    assert res["region"] == "Кемер"
    assert res["confidence"] <= 0.5