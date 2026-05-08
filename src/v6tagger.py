import spacy
import psycopg2
from collections import defaultdict

DB_CONFIG = {
    "dbname": "jokes",
    "user": "postgres",
    "password": "postgres",
    "host": "localhost",
    "port": 5433
}

nlp = spacy.load("ru_core_news_sm")


def get_conn():
    return psycopg2.connect(**DB_CONFIG)


def load_jokes():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, text FROM jokes")
            return cur.fetchall()


def extract_entities(text):
    doc = nlp(text)

    entities = set()

    for ent in doc.ents:
        # нормализация
        entity = ent.text.strip().lower().capitalize()

        # фильтр мусора
        if len(entity) < 3:
            continue

        if ent.label_ in ["PER", "ORG", "LOC", "MISC"]:
            entities.add(entity)

    return list(entities)


def save_entities(joke_id, entities):
    with get_conn() as conn:
        with conn.cursor() as cur:

            for entity in entities:

                # 1. создаём/ищем тег
                cur.execute("SELECT id FROM tags WHERE name = %s", (entity,))
                res = cur.fetchone()

                if res:
                    tag_id = res[0]
                else:
                    cur.execute(
                        "INSERT INTO tags (name) VALUES (%s) RETURNING id",
                        (entity,)
                    )
                    tag_id = cur.fetchone()[0]

                # 2. связываем
                cur.execute("""
                    INSERT INTO joke_tags (joke_id, tag_id)
                    VALUES (%s, %s)
                    ON CONFLICT DO NOTHING
                """, (joke_id, tag_id))

        conn.commit()


def run():
    jokes = load_jokes()
    print("Loaded:", len(jokes))

    for joke_id, text in jokes:
        entities = extract_entities(text)

        if entities:
            save_entities(joke_id, entities)

    print("DONE")


if __name__ == "__main__":
    run()