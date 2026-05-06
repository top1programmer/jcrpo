import psycopg2
from collections import defaultdict
import numpy as np

from sentence_transformers import SentenceTransformer
import hdbscan
from keybert import KeyBERT
from sklearn.cluster import AgglomerativeClustering

# =========================
# DB CONFIG
# =========================

DB_CONFIG = {
    "dbname": "jokes",
    "user": "postgres",
    "password": "postgres",
    "host": "localhost",
    "port": 5433
}


def get_conn():
    return psycopg2.connect(**DB_CONFIG)


def load_jokes():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, text FROM jokes")
            return cur.fetchall()


def load_existing_tags():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, name FROM tags")
            return {name: tid for tid, name in cur.fetchall()}


# =========================
# MODELS
# =========================

print("Loading models...")

embedder = SentenceTransformer("paraphrase-multilingual-MiniLM-L12-v2")
kw_model = KeyBERT(embedder)


# =========================
# CLEANING
# =========================

STOPWORDS = {
    "и", "в", "на", "с", "по", "не", "это", "как",
    "я", "ты", "он", "она", "они", "мы", "вы",
    "а", "но", "что", "у", "за", "из", "для",
    "бы", "если", "к", "от", "во"
}


def clean_text(text):
    return text.strip().lower()


# =========================
# CLUSTERING (HDBSCAN)
# =========================

def cluster_embeddings(X):
    model = AgglomerativeClustering(
        n_clusters=25
    )
    return model.fit_predict(X)

# =========================
# KEYWORD EXTRACTION
# =========================

def extract_keywords(texts):
    text = " ".join(texts)

    keywords = kw_model.extract_keywords(
        text,
        keyphrase_ngram_range=(1, 1),  # 🔥 ВАЖНО: только одиночные слова
        stop_words="russian",
        top_n=5
    )

    return [k[0] for k in keywords]

# =========================
# DB WRITE
# =========================

def save_tag(conn, tag_name, cache):
    with conn.cursor() as cur:
        if tag_name not in cache:
            cur.execute(
                "INSERT INTO tags (name) VALUES (%s) RETURNING id",
                (tag_name,)
            )
            cache[tag_name] = cur.fetchone()[0]
    return cache[tag_name]


def save_relation(conn, joke_id, tag_id):
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO joke_tags (joke_id, tag_id)
            VALUES (%s, %s)
            ON CONFLICT DO NOTHING
        """, (joke_id, tag_id))


# =========================
# PIPELINE
# =========================

def run():
    jokes = load_jokes()

    ids = [j[0] for j in jokes]
    texts = [clean_text(j[1]) for j in jokes]

    print(f"Loaded jokes: {len(jokes)}")

    print("Embedding...")
    X = embedder.encode(texts, show_progress_bar=True)

    print("Clustering (HDBSCAN)...")
    labels = cluster_embeddings(X)

    clusters = defaultdict(list)

    for idx, label in enumerate(labels):
        if label == -1:
            continue  # шум убираем
        clusters[label].append(idx)

    tag_cache = load_existing_tags()

    with get_conn() as conn:

        for cluster_id, indices in clusters.items():

            cluster_texts = [texts[i] for i in indices]

            tag_name = extract_tag_name(cluster_texts)

            print(f"Cluster {cluster_id} -> {tag_name}")

            tag_id = save_tag(conn, tag_name, tag_cache)

            for i in indices:
                save_relation(conn, ids[i], tag_id)

        conn.commit()

    print("DONE")


if __name__ == "__main__":
    run()