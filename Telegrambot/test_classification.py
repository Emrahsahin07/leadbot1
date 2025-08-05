import asyncio
import json
from ai_utils import classify_text_with_ai, get_openai_client
from dotenv import load_dotenv
load_dotenv()  # подгружает переменные из .env в окружение
from datetime import datetime

CONF_THRESHOLD = 0.8  # confidence threshold for auto-accepting leads
NEGATIVE_CLUES = ["думаю", "просто смотрю", "пока не готов", "рассматриваю", "не готов", "пока не буду"]

TRANSFER_KEYWORDS = [
    "из аэропорта", "в аэропорт", "шаттл", "такси", "подвоз", "встреча", "подвезти", "поездка", "водитель"
]

# normalize category/region for comparison
def normalize(s):
    if s is None:
        return None
    return s.strip().lower()

def generate_variations(base_tests):
    variants = []
    seen = set()
    for text, expected in base_tests:
        # copy expected to avoid shared mutation
        exp_copy = {k: expected.get(k) for k in expected}
        for variant in [text,]:
            key = (variant, exp_copy.get("relevant"), exp_copy.get("category"), exp_copy.get("region"))
            if key not in seen:
                variants.append((variant, exp_copy))
                seen.add(key)
        low_text = text.lower()
        if "хочу узнать" in low_text:
            v = low_text.replace("хочу узнать", "подскажите")
            key = (v, exp_copy.get("relevant"), exp_copy.get("category"), exp_copy.get("region"))
            if key not in seen:
                variants.append((v, exp_copy.copy()))
                seen.add(key)
        if "нужен" in low_text:
            v = low_text.replace("нужен", "хочу")
            key = (v, exp_copy.get("relevant"), exp_copy.get("category"), exp_copy.get("region"))
            if key not in seen:
                variants.append((v, exp_copy.copy()))
                seen.add(key)
        if any(w in low_text for w in ("снимать", "снять")):
            v = text + " пока просто рассматриваю"
            alt_expected = {"relevant": False, "category": expected.get("category"), "region": expected.get("region")}
            key = (v, alt_expected.get("relevant"), alt_expected.get("category"), alt_expected.get("region"))
            if key not in seen:
                variants.append((v, alt_expected))
                seen.add(key)
    return variants

# Post-hoc rules for ambiguous cases
def apply_posthoc_rules(text, category, region, override):
    low = text.lower()
    # трансфер-ключевые фразы
    transfer_triggers = ["из аэропорта", "в аэропорт", "шаттл", "такси", "подвезти", "поездка", "встреча"]
    if any(trigger in low for trigger in transfer_triggers):
        if category != "трансфер":
            override = (override or "") + " post-hoc transfer override"
        category = "трансфер"
    # ВНЖ
    vnj_triggers = ["внж", "гражданство", "резидентство", "документы", "иммиграция", "оформить внж", "как получить внж"]
    if any(trigger in low for trigger in vnj_triggers):
        if category != "внж":
            override = (override or "") + " post-hoc внж override"
        category = "внж"
    # Страховка
    if "страховк" in low:
        if category != "страховка":
            override = (override or "") + " post-hoc страховка override"
        category = "страховка"
    return category, override

def apply_negative_clues(text, relevant, category):
    low = text.lower()
    if any(clue in low for clue in NEGATIVE_CLUES):
        # soften or override to non-lead when there is no strong positive intent
        if relevant and category in ("недвижимость", "аренда", "аренда авто", "трансфер", "экскурсии"):
            relevant = False
    return relevant

# Загрузка реальных категорий и локаций как в продакшене
with open("categories.json", "r", encoding="utf-8") as f:
    categories_dict = json.load(f)

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
CANONICAL_LOCATIONS = sorted(set(LOCATION_ALIAS.values()))

TESTS = [
    # Прямые лиды
    ("Срочно нужен трансфер из аэропорта Анталии до отеля завтра вечером", {"relevant": True, "category": "трансфер", "region": "Анталия"}),
    ("Хочу взять индивидуальную экскурсию по Каппадокии на выходных", {"relevant": True, "category": "экскурсии", "region": "Каппадокия"}),
    ("Какие документы нужно собрать для оформления ВНЖ в Турции?", {"relevant": True, "category": "внж", "region": "Турция"}),
    ("Ищу квартиру на долгий срок в Стамбуле, можно с мебелью", {"relevant": True, "category": "недвижимость", "region": "Стамбул"}),
    ("Хочу арендовать машину в Аланье на неделю", {"relevant": True, "category": "аренда авто", "region": "Аланья"}),
    ("Сколько стоит страховка путешественника в Турции?", {"relevant": True, "category": "страховка", "region": "Турция"}),
    ("Где лучше обменять валюту в Манавгате?", {"relevant": True, "category": None, "region": "Манавгат"}),
    ("Нужен водитель для трансфера из Каша в Анталию послезавтра", {"relevant": True, "category": "трансфер", "region": "Анталия"}),
    ("Подскажите, как получить резидентство в Турции — какие шаги?", {"relevant": True, "category": "внж", "region": "Турция"}),
    ("Ищу уборщика для квартиры 3+1 в Лимане на постоянной основе", {"relevant": True, "category": "клининг", "region": "Анталия"}),

    # Отказы / не-лиды
    ("Пока просто смотрю варианты жилья в Стамбуле, не готов решать", {"relevant": False, "category": "недвижимость", "region": "Стамбул"}),
    ("Не интересует трансфер, спасибо", {"relevant": False, "category": "трансфер", "region": None}),
    ("Рассматриваю экскурсии на будущее, пока ничего не заказываю", {"relevant": False, "category": "экскурсии", "region": None}),
    ("Просто обсуждаем, как было в прошлый раз в Кемере", {"relevant": False, "category": "экскурсии", "region": "Кемер"}),
    ("Сейчас не планирую оформление ВНЖ, просто читаю информацию", {"relevant": False, "category": "внж", "region": None}),

    # Реклама / спам
    ("VIP трансфер из аэропорта: бронируйте сейчас! Контакты: @vipdriver", {"relevant": False, "category": "трансфер", "region": None}),
    ("Экскурсия в Памуккале по сниженной цене, звоните и бронируйте у нас!", {"relevant": False, "category": "экскурсии", "region": None}),
    ("Сдаю 2+1 в Кемере без посредников, 550€, звоните на WhatsApp +90...", {"relevant": False, "category": "недвижимость", "region": "Кемер"}),
    ("Оформим ВНЖ быстро! Все документы под ключ, пишите в личку", {"relevant": False, "category": "внж", "region": None}),
    ("Страховка для туристов — спецпредложение! Купи сейчас и сэкономь", {"relevant": False, "category": "страховка", "region": None}),

    # Смешанные / пограничные случаи
    ("Хочу трансфер, но пока просто сравниваю цены", {"relevant": False, "category": "трансфер", "region": None}),
    ("Есть интерес к экскурсии, но не уверен, когда поеду", {"relevant": False, "category": "экскурсии", "region": None}),
    ("Нужен трансфер из аэропорта, подскажите варианты и цены", {"relevant": True, "category": "трансфер", "region": None}),
    ("Продаём экскурсии в Стамбуле дешевле всех, бронируйте у нас!", {"relevant": False, "category": "экскурсии", "region": "Стамбул"}),
    ("Хочу узнать про жильё в Анталии, может снять на месяц", {"relevant": True, "category": "недвижимость", "region": "Анталия"}),
    ("Интересует аренда авто, но пока просто смотрю предложения", {"relevant": False, "category": "аренда авто", "region": None}),
]

TESTS_COMPETITOR = [
    ("Сдаю дом на долгий срок в Аланье, 3+1, вид на море, без комиссии", {"relevant": False, "category": "недвижимость", "region": "Аланья"}),
    ("Ищу краткосрочную аренду квартиры в Анталии, до 500€ за ночь", {"relevant": True, "category": "недвижимость", "region": "Анталия"}),
    ("Какие есть варианты трансфера из аэропорта в Сиде?", {"relevant": True, "category": "трансфер", "region": "Сиде"}),
    ("Хочу прокатиться на яхте, кто организует экскурсию?", {"relevant": True, "category": "экскурсии", "region": None}),
    ("Предлагаем услуги клининга в Анталии, контакт в описании", {"relevant": False, "category": "клининг", "region": "Анталия"}),
    ("Ищу дом в Анталии на долгий срок, можно с видом на море", {"relevant": True, "category": "недвижимость", "region": "Анталия"}),
    ("Продаётся 2+1 в Кемере без посредников, 550€ в месяц", {"relevant": False, "category": "недвижимость", "region": "Кемер"}),
    ("Рассматриваю недвижимость в Стамбуле, просто собираю информацию", {"relevant": False, "category": "недвижимость", "region": "Стамбул"}),
]

async def run_tests():
    results = []
    # Prepare OpenAI client once
    client_ai = get_openai_client()
    categories_keys = list(categories_dict.keys())

    augmented = generate_variations(TESTS)
    augmented_comp = generate_variations(TESTS_COMPETITOR)

    for text, expected in augmented:
        # Always pass text, categories, locations, client_ai
        cla = await asyncio.to_thread(
            classify_text_with_ai,
            text,
            categories_keys,
            CANONICAL_LOCATIONS,
            client_ai
        )
        if not isinstance(cla, dict):
            print(f"[ERROR] No classification for: {text}")
            continue
        # Extract fields
        relevant = cla.get("relevant")
        category = cla.get("category")
        region = cla.get("region")
        confidence = cla.get("confidence", 0)
        explanation = cla.get("explanation", "")
        override = cla.get("override_reason", "")

        # Apply negative clue adjustment (weakens apparent leads when phrased as passive/considering)
        relevant = apply_negative_clues(text, relevant, category)
        # Apply post-hoc overrides (transfer, внж, страховка, etc.)
        category, override = apply_posthoc_rules(text, category, region, override)

        # Compare to expected (loose for None, normalized)
        ok_relevant = relevant == expected["relevant"]
        ok_category = (expected["category"] is None and category is None) or (normalize(category) == normalize(expected.get("category")))
        ok_region = (expected.get("region") is None and region is None) or (normalize(region) == normalize(expected.get("region")))

        result = {
            "text": text,
            "expected": expected,
            "got": {
                "relevant": relevant,
                "category": category,
                "region": region,
                "confidence": confidence,
                "explanation": explanation,
                "override_reason": override,
            },
            "matches": {
                "relevant": ok_relevant,
                "category": ok_category,
                "region": ok_region,
            },
            "timestamp": datetime.now().strftime("%m-%d %H:%M"),
        }
        results.append(result)
        status = "✅" if ok_relevant and ok_category and ok_region else "❌"
        print(f"{status} [{confidence:.2f}] '{text}' -> relevant={relevant}, category={category}, region={region}, override={override}")

    # Competitor tests
    for text, expected in augmented_comp:
        cla = await asyncio.to_thread(
            classify_text_with_ai,
            text,
            categories_keys,
            CANONICAL_LOCATIONS,
            client_ai
        )
        if not isinstance(cla, dict):
            print(f"[ERROR] No classification for: {text}")
            continue
        relevant = cla.get("relevant")
        category = cla.get("category")
        region = cla.get("region")
        confidence = cla.get("confidence", 0)
        explanation = cla.get("explanation", "")
        override = cla.get("override_reason", "")

        # Apply negative clue adjustment (weakens apparent leads when phrased as passive/considering)
        relevant = apply_negative_clues(text, relevant, category)
        # Apply post-hoc overrides (transfer, внж, страховка, etc.)
        category, override = apply_posthoc_rules(text, category, region, override)

        ok_relevant = relevant == expected["relevant"]
        ok_category = (expected["category"] is None and category is None) or (normalize(category) == normalize(expected.get("category")))
        ok_region = (expected.get("region") is None and region is None) or (normalize(region) == normalize(expected.get("region")))

        result = {
            "group": "competitor",
            "text": text,
            "expected": expected,
            "got": {
                "relevant": relevant,
                "category": category,
                "region": region,
                "confidence": confidence,
                "explanation": explanation,
                "override_reason": override,
            },
            "matches": {
                "relevant": ok_relevant,
                "category": ok_category,
                "region": ok_region,
            },
            "timestamp": datetime.now().strftime("%m-%d %H:%M"),
        }
        results.append(result)
        status = "✅" if ok_relevant and ok_category and ok_region else "❌"
        print(f"[COMP] {status} [{confidence:.2f}] '{text}' -> relevant={relevant}, category={category}, region={region}, override={override}")

    print("\nConfidence distribution:")
    # build buckets
    dists = {"<0.5":0, "0.5-0.6":0, "0.6-0.7":0, "0.7-0.8":0, ">=0.8":0}
    for r in results:
        c = r.get("got", {}).get("confidence", 0)
        if c < 0.5:
            dists["<0.5"] += 1
        elif c < 0.6:
            dists["0.5-0.6"] += 1
        elif c < 0.7:
            dists["0.6-0.7"] += 1
        elif c < 0.8:
            dists["0.7-0.8"] += 1
        else:
            dists[">=0.8"] += 1
    for k,v in dists.items():
        pct = v / len(results) * 100 if results else 0
        print(f"  {k}: {v} ({pct:.1f}%)")

    # Calibration summary: how confidence maps to actual relevant correctness
    calibration = {}
    bins = [(0,0.5), (0.5,0.6), (0.6,0.7), (0.7,0.8), (0.8,1.01)]
    for low, high in bins:
        bucket = [r for r in results if low <= r.get("got", {}).get("confidence", 0) < high]
        if not bucket:
            continue
        total = len(bucket)
        correct_relevant = sum(1 for r in bucket if r.get("expected", {}).get("relevant") == r.get("got", {}).get("relevant"))
        # false negatives and positives for relevant
        fn = [r for r in bucket if r.get("expected", {}).get("relevant") and not r.get("got", {}).get("relevant")]
        fp = [r for r in bucket if not r.get("expected", {}).get("relevant") and r.get("got", {}).get("relevant")]
        calibration[f"{low}-{high}"] = {
            "count": total,
            "accuracy": correct_relevant / total,
            "false_negatives": len(fn),
            "false_positives": len(fp),
        }
    print("\nCalibration:")
    for k, v in calibration.items():
        print(f"  {k}: {v['count']} examples, relevant accuracy {v['accuracy']:.2f}, FN {v['false_negatives']}, FP {v['false_positives']}")
    with open("confidence_calibration.json", "w", encoding="utf-8") as cf:
        json.dump(calibration, cf, ensure_ascii=False, indent=2)

    # Dump full detailed report to JSON
    with open("classification_test_report.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print("\nСохранён подробный отчёт в classification_test_report.json")

    # Log questionable cases below threshold
    low_confidence_issues = [r for r in results if r.get("got", {}).get("confidence", 0) < CONF_THRESHOLD and r.get("got", {}).get("relevant")]
    if low_confidence_issues:
        print(f"\nLeads with confidence below threshold {CONF_THRESHOLD} but marked relevant: {len(low_confidence_issues)}")
        with open("low_confidence_leads.json", "w", encoding="utf-8") as lf:
            json.dump(low_confidence_issues, lf, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    asyncio.run(run_tests())