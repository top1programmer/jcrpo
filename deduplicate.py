import psycopg2
import numpy as np
import ast
from sentence_transformers import SentenceTransformer
from tqdm import tqdm



DB_CONFIG = {
    "dbname": "jokes",
    "user": "postgres",
    "password": "postgres",
    "host": "localhost",
    "port": 5433
}

model = SentenceTransformer("paraphrase-multilingual-MiniLM-L12-v2")


def get_conn():
    return psycopg2.connect(**DB_CONFIG)


def cosine_sim(a, b):
    return np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b))


def find_duplicates(batch_size=100):
    conn = get_conn()
    cur = conn.cursor()

    print("Loading jokes...")
    cur.execute("""
        SELECT id, text, embedding
        FROM jokes
        WHERE embedding IS NOT NULL
    """)
    jokes = cur.fetchall()

    print(f"Loaded: {len(jokes)}")

    duplicates = []

    for i, (id1, text1, emb1) in enumerate(tqdm(jokes)):
        # 🔹 1. trigram кандидаты
        cur.execute("""
            SELECT id, text, embedding
            FROM jokes
            WHERE id != %s
              AND similarity(text, %s) > 0.4
            LIMIT 20
        """, (id1, text1))

        candidates = cur.fetchall()

        for id2, text2, emb2 in candidates:
            if not emb2:
                continue

            vec1 = np.array(ast.literal_eval(emb1), dtype=np.float32)
            vec2 = np.array(ast.literal_eval(emb2), dtype=np.float32)

            sim = cosine_sim(vec1, vec2)

            # 🔥 финальный порог
            if sim > 0.9:
                duplicates.append((id1, id2, sim))

    cur.close()
    conn.close()

    return duplicates


if __name__ == "__main__":
    dups = find_duplicates()

    print(f"\nFound duplicates: {len(dups)}")

    for d in dups[:20]:
        print(d)