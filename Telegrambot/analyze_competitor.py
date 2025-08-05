# analyze_competitor.py
import json
from collections import Counter, defaultdict
import re
import argparse
import math

try:
    import pymorphy2
    # monkey-patch inspect for compatibility if needed (older pymorphy2 uses getargspec)
    import inspect
    if not hasattr(inspect, 'getargspec'):
        inspect.getargspec = inspect.getfullargspec  # fallback shim
    morph = pymorphy2.MorphAnalyzer()
except Exception:
    morph = None  # lemmatization unavailable

# Speech-act markers (compiled for performance)
REQUEST_MARKERS = [re.compile(p, re.IGNORECASE) for p in [
    r"\bхочу\b", r"\bищу\b", r"\bнужн[аоы]?\b", r"\bсколько стоит\b", r"\bподскажите\b", r"\bсниму\b",
    r"\bнужна\b", r"\bпомогите\b", r"\bкак добраться\b", r"\bгде взять\b"
]]
OFFER_MARKERS = [re.compile(p, re.IGNORECASE) for p in [
    r"\bсдам\b", r"\bпрода(?:ёт|ётся|ется)\b", r"\bпредлага(?:ем|ется)\b", r"\bузнать стоимость\b", r"\bбез комиссии\b",
    r"\bдепозит\b", r"\bот собственника\b", r"\bаренда недвижимость\b"
]]

# stopwords to drop completely from analysis
BASE_STOPWORDS = {
    "возможный", "регион"
}
# filler / high-frequency low-value tokens; can be expanded from analysis
FILLER_STOPWORDS = {
    "на","до","есть","от","для","по","в","с","и","это","так","еще","хорошо","очень","без","или","как","что","кто","где","когда",
    "почему","зачем","только","все","всегда","всё","рядом","не","000","из","за","то","здравствуйте","парковка","зона","день","добрый","метров",
    "кафе","можно","пожалуйста","августа","детская","район","месяц","евро","море","phone","цена","моря","полностью","открытый","лир","районе",
    "свободна","балкон","минут","новый","рынок","всего","оба","мин","пишите","барбекю","магазины","интернет","площадка","варианты","если"
}

# canonical region names
CANONICAL_REGIONS = [
    "Анталия", "Бельдиби", "Гёйнюк", "Кемер", "Манавгат", "Сиде", "Стамбул", "Турция",
    "Чамьюва", "Аланья", "Мерсин", "Измир", "Бурса", "Анкара"
]
REGION_PATTERNS = [re.compile(r"\b" + re.escape(region) + r"\b", re.IGNORECASE) for region in CANONICAL_REGIONS]

# normalization + lemmatization

def normalize_token(token: str) -> str:
    token = token.lower()
    if token in BASE_STOPWORDS or token in FILLER_STOPWORDS:
        return ""
    if morph:
        try:
            p = morph.parse(token)[0]
            return p.normal_form
        except Exception:
            return token
    return token

# remove group header (text before first double newline) and possible 'Возможный регион' block

def strip_metadata(text: str) -> str:
    # drop everything before first blank line (group name/header)
    parts = re.split(r"\n\s*\n", text, maxsplit=1)
    core = parts[1] if len(parts) > 1 else parts[0]
    # remove trailing 'Возможный регион' sections if present
    core = re.sub(r"Возможный\s+регион:.*", "", core, flags=re.IGNORECASE|re.DOTALL)
    return core

# tokenize text into normalized meaningful tokens

def tokenize(text: str):
    text = strip_metadata(text)
    # strip hashtags (#word -> word)
    text = re.sub(r"#([\wа-яёА-ЯЁ]+)", r"\1", text)
    # remove urls and user handles
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"@[\w_]+", "", text)
    raw_tokens = re.findall(r"\b[а-яёa-z0-9]+\b", text.lower())
    tokens = [normalize_token(t) for t in raw_tokens]
    return [t for t in tokens if t and len(t) >= 2]


def contains_any(regex_list, text: str) -> bool:
    for pat in regex_list:
        if pat.search(text):
            return True
    return False


def extract_regions(text: str):
    found = []
    for region, pattern in zip(CANONICAL_REGIONS, REGION_PATTERNS):
        if pattern.search(text):
            found.append(region)
    return found


def compute_idf(df_counter: Counter, total_docs: int) -> dict:
    return {term: math.log((total_docs + 1) / (df + 1)) + 1 for term, df in df_counter.items()}


def main(path, top_n=50, export=None):
    total = 0
    speech_act_counts = defaultdict(int)
    messages = []  # store all parsed messages for later aggregation
    region_counter = Counter()
    unigram_df = Counter()
    bigram_df = Counter()

    for line in open(path, encoding="utf-8"):
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        raw_text = item.get("text", "")
        total += 1
        clean_text = strip_metadata(raw_text)
        tokens = tokenize(raw_text)
        filtered_tokens = [t for t in tokens if t not in BASE_STOPWORDS and t not in FILLER_STOPWORDS]

        # speech act classification
        is_request = contains_any(REQUEST_MARKERS, raw_text)
        is_offer = contains_any(OFFER_MARKERS, raw_text)
        if is_request and not is_offer:
            speech_act_counts["request_only"] += 1
        elif is_offer and not is_request:
            speech_act_counts["offer_only"] += 1
        elif is_request and is_offer:
            speech_act_counts["mixed"] += 1
        else:
            speech_act_counts["neutral"] += 1

        regions_found = extract_regions(raw_text)
        region_counter.update(regions_found)

        # document frequency
        unigram_df.update(set(filtered_tokens))
        bigram_set = set((filtered_tokens[i], filtered_tokens[i+1]) for i in range(len(filtered_tokens) - 1))
        bigram_df.update(bigram_set)

        messages.append({
            "text": raw_text,
            "clean_text": clean_text,
            "tokens": tokens,
            "filtered_tokens": filtered_tokens,
            "is_request": is_request,
            "is_offer": is_offer,
            "regions": regions_found,
        })

    # aggregate counts
    all_tokens = []
    for msg in messages:
        all_tokens.extend(msg["tokens"])
    unfiltered_word_counter = Counter(all_tokens)
    filtered_word_counter = Counter(t for t in all_tokens if t not in BASE_STOPWORDS and t not in FILLER_STOPWORDS)

    # bigrams
    all_bigrams = [(all_tokens[i], all_tokens[i+1]) for i in range(len(all_tokens) - 1)]
    unfiltered_bigram_counter = Counter(all_bigrams)
    filtered_bigram_counter = Counter((a, b) for (a, b) in all_bigrams if a not in BASE_STOPWORDS and a not in FILLER_STOPWORDS and b not in BASE_STOPWORDS and b not in FILLER_STOPWORDS)

    # TF-IDF
    unigram_idf = compute_idf(unigram_df, total)
    bigram_idf = compute_idf(bigram_df, total)

    unigram_tfidf = Counter()
    bigram_tfidf = Counter()

    for msg in messages:
        ftokens = msg["filtered_tokens"]
        tf_unigram = Counter(ftokens)
        tf_bigram = Counter((ftokens[i], ftokens[i+1]) for i in range(len(ftokens) - 1))
        for term, tf in tf_unigram.items():
            idf = unigram_idf.get(term, math.log((total + 1) / 1) + 1)
            unigram_tfidf[term] += tf * idf
        for term, tf in tf_bigram.items():
            idf = bigram_idf.get(term, math.log((total + 1) / 1) + 1)
            bigram_tfidf[term] += tf * idf

    top_unigrams_tfidf = unigram_tfidf.most_common(top_n)
    top_bigrams_tfidf = bigram_tfidf.most_common(top_n)

    # noise recommendation: high document frequency but low tfidf/freq ratio
    noise_candidates = []
    for term, freq in filtered_word_counter.items():
        df = unigram_df.get(term, 0)
        tfidf_score = dict(top_unigrams_tfidf).get(term, 0)
        ratio = tfidf_score / (freq + 1e-6)
        noise_candidates.append((term, freq, ratio, df))
    noise_candidates.sort(key=lambda x: (x[2], -x[1]))  # small ratio = likely noise
    noise_recommendations = [
        {"term": t, "freq": f, "ratio": r, "df": d} for t, f, r, d in noise_candidates[: top_n]
    ]

    # candidate keywords (by high tf-idf)
    candidate_keywords = [
        {"term": t, "tfidf": score, "df": unigram_df.get(t, 0)} for t, score in top_unigrams_tfidf
    ]

    summary = {
        "total_messages": total,
        "speech_act_breakdown": dict(speech_act_counts),
        "top_unigrams_filtered": filtered_word_counter.most_common(top_n),
        "top_bigrams_filtered": [((a, b), cnt) for (a, b), cnt in filtered_bigram_counter.most_common(top_n)],
        "top_unigrams_tfidf": top_unigrams_tfidf,
        "top_bigrams_tfidf": top_bigrams_tfidf,
        "top_regions": region_counter.most_common(top_n),
        "noise_recommendations": noise_recommendations,
        "candidate_keywords": candidate_keywords,
    }

    # optional: coverage if categories.json exists
    coverage = {}
    try:
        with open('categories.json', encoding='utf-8') as cf:
            cats = json.load(cf)
        for cat_name, cat_info in cats.items():
            keywords = cat_info.get('keywords', [])
            kw_set = set(k.lower() for k in keywords)
            cat_count = 0
            region_counts = defaultdict(int)
            for msg in messages:
                toks = set(msg['filtered_tokens'])
                if toks & kw_set:
                    cat_count += 1
                    for reg in msg.get('regions', []):
                        region_counts[reg] += 1
            coverage[cat_name] = {
                'total': cat_count,
                'by_region': dict(region_counts)
            }
        summary['coverage'] = coverage
    except FileNotFoundError:
        pass

    # human-readable output
    print(f"Total messages: {total}\n")
    print("Speech-act breakdown:")
    for k, v in speech_act_counts.items():
        print(f"  {k}: {v}")
    print(f"\nTop {top_n} unigrams (filtered):")
    for word, cnt in filtered_word_counter.most_common(top_n):
        print(f"  {word}: {cnt}")
    print(f"\nTop {top_n} unigrams by TF-IDF:")
    for word, score in top_unigrams_tfidf:
        print(f"  {word}: {score:.4f}")
    print(f"\nNoise candidates (low tfidf/freq):")
    for n in noise_recommendations[: min(20, len(noise_recommendations))]:
        print(f"  {n['term']}: freq={n['freq']} ratio={n['ratio']:.2f} df={n['df']}")

    print(f"\nCandidate keywords:")
    shown = 0
    for c in candidate_keywords:
        term = c['term']
        if term in BASE_STOPWORDS or term in FILLER_STOPWORDS:
            continue
        print(f"  {term}: tfidf={c['tfidf']:.2f} df={c['df']}")
        shown += 1
        if shown >= min(30, len(candidate_keywords)):
            break

    print(f"\nTop {top_n} regions:")
    for region, cnt in region_counter.most_common(top_n):
        print(f"  {region}: {cnt}")
    if 'coverage' in summary:
        print('\nCoverage by category:')
        for cat, info in summary['coverage'].items():
            print(f"  {cat}: total={info['total']}")
            for reg, cnt in info['by_region'].items():
                print(f"    {reg}: {cnt}")

    if export:
        with open(export, "w", encoding="utf-8") as outf:
            json.dump(summary, outf, ensure_ascii=False, indent=2)
        print(f"Exported summary to {export}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("path", help="Path to competitor leads .jsonl file")
    parser.add_argument("--top", type=int, default=30)
    parser.add_argument("--export", help="Path to dump JSON summary of counts")
    args = parser.parse_args()
    main(args.path, top_n=args.top, export=args.export)