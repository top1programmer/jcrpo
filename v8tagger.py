import re
import psycopg2
from collections import defaultdict
import spacy
import pymorphy3

DB_CONFIG = {
    "dbname": "jokes",
    "user": "postgres",
    "password": "postgres",
    "host": "localhost",
    "port": 5433
}

# --- NLP models ---
nlp = spacy.load("ru_core_news_sm")
morph = pymorphy3.MorphAnalyzer()

# --- stop junk ---
STOPWORDS = {
    "это", "как", "что", "все", "так", "уже", "только", "очень",
    "просто", "ещё", "бы", "не", "ни", "на", "в", "и", "а", "но"
}

# --- seed tags ---
SEED_TAGS = {
    "трамп": {"трамп", "donald trump"},
    "медицина": {"врач", "больница", "операция", "антибиотик"},
    "луна": {"луна", "космос", "спутник"},
    "война": {"война", "армия", "конфликт"},
    "политика": {"конгресс", "госдеп", "президент"},
    "секс": {"секс", "порно", "эротика"},
    "алкоголь": {"пиво", "водка", "выпил"},
    "программисты": {"код", "python", "разработчик"},
}

def get_conn():
    return psycopg2.connect(**DB_CONFIG)

def load_jokes():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, text FROM jokes")
            return cur.fetchall()

# --- normalization ---
def normalize(word: str):
    word = word.lower().strip()
    if word in STOPWORDS:
        return None
    parsed = morph.parse(word)[0]
    return parsed.normal_form

# --- entity extraction ---
def extract_entities(text):
    doc = nlp(text)
    entities = []

    for ent in doc.ents:
        if ent.label_ in {"PER", "ORG", "LOC"}:
            entities.append(ent.text.lower())

    return entities

# --- tokenize ---
def tokenize(text):
    return re.findall(r"[a-zа-яё]+", text.lower())

# --- build dictionary ---
def build_dictionary(jokes):
    stats = defaultdict(int)

    for _, text in jokes:
        words = tokenize(text)

        # entities first
        for ent in extract_entities(text):
            stats[ent] += 3  # boost entities

        for w in words:
            norm = normalize(w)
            if not norm:
                continue
            stats[norm] += 1

    # filter noise
    return {k for k, v in stats.items() if v >= 3}

# --- auto tagging ---
def auto_tag(text, dictionary):
    tags = set()

    words = set(normalize(w) for w in tokenize(text))
    entities = set(extract_entities(text))

    words = {w for w in words if w}

    for tag in dictionary:
        if tag in words or tag in entities:
            tags.add(tag)

    return list(tags)

# --- save ---
def save_tags(joke_id, tags):
    with get_conn() as conn:
        with conn.cursor() as cur:

            for tag in tags:
                cur.execute("SELECT id FROM tags WHERE name = %s", (tag,))
                res = cur.fetchone()

                if not res:
                    cur.execute(
                        "INSERT INTO tags (name) VALUES (%s) RETURNING id",
                        (tag,)
                    )
                    tag_id = cur.fetchone()[0]
                else:
                    tag_id = res[0]

                cur.execute("""
                    INSERT INTO joke_tags (joke_id, tag_id)
                    VALUES (%s, %s)
                    ON CONFLICT DO NOTHING
                """, (joke_id, tag_id))

        conn.commit()

def run():
    jokes = load_jokes()
    print("Loaded:", len(jokes))

    print("Building dictionary...")
    dictionary = build_dictionary(jokes)

    print("Dictionary size:", len(dictionary))

    for joke_id, text in jokes:
        tags = auto_tag(text, dictionary)
        if tags:
            save_tags(joke_id, tags)

    print("DONE")

if __name__ == "__main__":
    run()