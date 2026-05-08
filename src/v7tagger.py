import re
import psycopg2
import numpy as np
import spacy
from sentence_transformers import SentenceTransformer

# ----------------------------
# DB CONFIG
# ----------------------------

DB_CONFIG = {
    "dbname": "jokes",
    "user": "postgres",
    "password": "postgres",
    "host": "localhost",
    "port": 5433
}

def get_conn():
    return psycopg2.connect(**DB_CONFIG)


# ----------------------------
# NLP MODELS
# ----------------------------

nlp = spacy.load("ru_core_news_sm")
model = SentenceTransformer("paraphrase-multilingual-MiniLM-L12-v2")


# ----------------------------
# DICTIONARY ENTITIES (SEMANTIC TAGS)
# ----------------------------

ENTITY_DICTIONARY = {
    "трамп": ["трамп", "donald trump"],
    "россия": ["россия", "рф", "российская федерация"],
    "медицина": ["врач", "больница", "антибиотики", "операция"],
    "секс": ["секс", "интим", "постель"],
    "блогер": ["блогер", "ютубер", "стример"],
    "традиция": ["традиция", "обычай"],
    "луна": ["луна", "лунный"]
}


# ----------------------------
# CLEANING
# ----------------------------

BAD_CHARS = re.compile(r"[^\w\sа-яА-ЯёЁ-]")


def clean(text: str) -> str:
    text = BAD_CHARS.sub("", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def is_valid_entity(text: str) -> bool:
    if len(text) < 3:
        return False
    if len(text.split()) > 2:
        return False
    if text.endswith("-") or text.endswith("?"):
        return False
    return True


# ----------------------------
# ENTITY EXTRACTION (spaCy)
# ----------------------------

def extract_spacy_entities(text: str):
    doc = nlp(text)
    entities = set()

    for ent in doc.ents:
        e = clean(ent.text)

        if not is_valid_entity(e):
            continue

        if ent.label_ in ["PER", "LOC", "ORG", "MISC"]:
            entities.add(e.capitalize())

    return entities


# ----------------------------
# DICTIONARY ENTITIES
# ----------------------------

def extract_dictionary_entities(text: str):
    text = text.lower()
    found = set()

    for key, variants in ENTITY_DICTIONARY.items():
        for v in variants:
            if v in text:
                found.add(key.capitalize())

    return found


# ----------------------------
# FULL ENTITY PIPELINE
# ----------------------------

def extract_entities(text: str):
    entities = set()

    entities |= extract_spacy_entities(text)
    entities |= extract_dictionary_entities(text)

    return list(entities)


# ----------------------------
# EMBEDDINGS
# ----------------------------

def get_embedding(text: str):
    return model.encode(text).tolist()


# ----------------------------
# DB LOAD
# ----------------------------

def load_jokes():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, text FROM jokes")
            return cur.fetchall()


# ----------------------------
# SAVE ENTITIES
# ----------------------------

def save_entities(joke_id, entities):
    with get_conn() as conn:
        with conn.cursor() as cur:

            for e in entities:

                cur.execute("SELECT id FROM tags WHERE name = %s", (e,))
                res = cur.fetchone()

                if res:
                    tag_id = res[0]
                else:
                    cur.execute(
                        "INSERT INTO tags (name) VALUES (%s) RETURNING id",
                        (e,)
                    )
                    tag_id = cur.fetchone()[0]

                cur.execute("""
                    INSERT INTO joke_tags (joke_id, tag_id)
                    VALUES (%s, %s)
                    ON CONFLICT DO NOTHING
                """, (joke_id, tag_id))

        conn.commit()


# ----------------------------
# SAVE EMBEDDING
# ----------------------------

def save_embedding(joke_id, embedding):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE jokes
                SET embedding = %s
                WHERE id = %s
            """, (embedding, joke_id))
        conn.commit()


# ----------------------------
# MAIN PIPELINE
# ----------------------------

def run():
    jokes = load_jokes()
    print("Loaded jokes:", len(jokes))

    for joke_id, text in jokes:

        # 1. entities
        entities = extract_entities(text)

        # 2. embedding
        embedding = get_embedding(text)

        # 3. save
        if entities:
            save_entities(joke_id, entities)

        save_embedding(joke_id, embedding)

    print("DONE")


# ----------------------------
# SEARCH TEST (optional)
# ----------------------------

def search(query, limit=10):
    q_emb = np.array(model.encode(query))

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, text, embedding FROM jokes")
            rows = cur.fetchall()

    scored = []

    for r in rows:
        emb = np.array(r[2])

        score = np.dot(q_emb, emb) / (np.linalg.norm(q_emb) * np.linalg.norm(emb))
        scored.append((score, r[1]))

    scored.sort(reverse=True, key=lambda x: x[0])

    return scored[:limit]


# ----------------------------
# ENTRYPOINT
# ----------------------------

if __name__ == "__main__":
    run()