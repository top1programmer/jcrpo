import psycopg2
from collections import defaultdict, Counter
import numpy as np

from sentence_transformers import SentenceTransformer
from sklearn.cluster import KMeans


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
# TEXT CLEANING (light)
# =========================

def clean(text: str):
    return text.strip().lower()


# =========================
# EMBEDDINGS MODEL
# =========================

model = SentenceTransformer("paraphrase-multilingual-MiniLM-L12-v2")


def embed_texts(texts):
    return model.encode(texts, show_progress_bar=True)


# =========================
# CLUSTERING
# =========================

def cluster_embeddings(X, n_clusters=20):
    kmeans = KMeans(n_clusters=n_clusters, random_state=42, n_init=10)
    labels = kmeans.fit_predict(X)
    return labels


# =========================
# TAG NAME GENERATION (KEY IDEA)
# =========================

def extract_tag_name(texts):
    """
    В v3 мы НЕ полагаемся на TF-IDF.
    Мы берем самые частые содержательные слова.
    """

    stopwords = {
        "и", "в", "на", "с", "по", "не", "это", "как",
        "я", "ты", "он", "она", "они", "мы", "вы",
        "а", "но", "что", "у", "за", "из", "для",
        "бы", "если", "к", "от", "во", "то"
    }

    counter = Counter()

    for t in texts:
        words = [
            w for w in t.lower().split()
            if w.isalpha() and w not in stopwords and len(w) > 3
        ]
        counter.update(words)

    top = [w for w, _ in counter.most_common(3)]

    if not top:
        return "unknown"

    return "_".join(top[:2])


# =========================
# DB SAVE
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
    texts = [clean(j[1]) for j in jokes]

    print(f"Loaded jokes: {len(jokes)}")

    print("Embedding texts...")
    X = embed_texts(texts)

    print("Clustering...")
    labels = cluster_embeddings(X, n_clusters=25)

    clusters = defaultdict(list)

    for idx, label in enumerate(labels):
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