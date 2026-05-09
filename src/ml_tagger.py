import psycopg2
import re
from collections import Counter
from tqdm import tqdm

import torch
import numpy as np
import spacy

from sentence_transformers import SentenceTransformer
from sklearn.cluster import DBSCAN

# -------------------------
# CONFIG
# -------------------------

DB_CONFIG = {
    "dbname": "jokes",
    "user": "postgres",
    "password": "postgres",
    "host": "localhost",
    "port": 5433
}

device = "cuda" if torch.cuda.is_available() else "cpu"
print("Device:", device)

model = SentenceTransformer(
    "paraphrase-multilingual-MiniLM-L12-v2",
    device=device
)

nlp = spacy.load("ru_core_news_sm", disable=["parser", "ner"])

# -------------------------
# STOPWORDS
# -------------------------

STOPWORDS = {
    "это","как","что","в","на","и","а","но","за","его","ее","бы","же",
    "ли","уже","он","она","они","мы","вы","я"
}

MIN_FREQ = 5

# -------------------------
# DB
# -------------------------

def conn():
    return psycopg2.connect(**DB_CONFIG)


def load_jokes(limit=20000):
    with conn() as c:
        with c.cursor() as cur:
            cur.execute("""
                SELECT id, text
                FROM jokes
                ORDER BY random()
                LIMIT %s
            """, (limit,))
            return cur.fetchall()

# -------------------------
# PHRASE EXTRACTION
# -------------------------

def extract_phrases(text):
    text = text.lower()
    words = re.findall(r"[а-яёa-z]+", text)

    if len(words) < 2:
        return []

    # bigrams + trigrams
    phrases = []
    phrases += [" ".join(words[i:i+2]) for i in range(len(words)-1)]
    phrases += [" ".join(words[i:i+3]) for i in range(len(words)-2)]

    return phrases


# -------------------------
# CLEANING
# -------------------------

def clean_phrases(phrases):
    out = []

    for p in phrases:
        if len(p) < 5:
            continue

        ws = p.split()

        if any(w in STOPWORDS for w in ws):
            continue

        # убираем мусор типа "по", "с", "в"
        if len(ws) < 2:
            continue

        out.append(p)

    return out


# -------------------------
# POS FILTER (FIXED)
# -------------------------

def pos_filter(words):
    docs = nlp.pipe(words)

    result = []

    for word, doc in zip(words, docs):
        ok = False
        for t in doc:
            if t.pos_ in {"NOUN", "PROPN"}:
                ok = True
        if ok:
            result.append(word)

    return result


# -------------------------
# VOCAB BUILD
# -------------------------

def build_vocab(jokes):
    counter = Counter()

    for _, text in tqdm(jokes, desc="extracting"):
        phrases = clean_phrases(extract_phrases(text))
        counter.update(phrases)

    vocab = [w for w, c in counter.items() if c >= MIN_FREQ]

    print("raw vocab:", len(vocab))

    # POS filter (IMPORTANT FIX)
    vocab = pos_filter(vocab)

    print("after POS filter:", len(vocab))

    return vocab


# -------------------------
# EMBEDDINGS + CLUSTERING
# -------------------------

def cluster_vocab(vocab):
    if len(vocab) == 0:
        return {}

    emb = model.encode(
        vocab,
        convert_to_tensor=True,
        device=device,
        normalize_embeddings=True,
        batch_size=256
    )

    emb_np = emb.cpu().numpy()

    clustering = DBSCAN(
        eps=0.22,
        min_samples=2,
        metric="cosine"
    ).fit(emb_np)

    clusters = {}

    for word, label in zip(vocab, clustering.labels_):
        if label == -1:
            continue
        clusters.setdefault(label, []).append(word)

    return clusters


# -------------------------
# SAVE TAGS
# -------------------------

def save_tags(clusters):
    tag_map = {}

    with conn() as c:
        with c.cursor() as cur:

            for words in clusters.values():

                if len(words) < 2:
                    continue

                main_tag = max(words, key=len)

                cur.execute("""
                    INSERT INTO tags (name)
                    VALUES (%s)
                    ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name
                    RETURNING id
                """, (main_tag,))

                tag_id = cur.fetchone()[0]

                tag_map[tag_id] = words

        c.commit()

    return tag_map


# -------------------------
# ASSIGN TAGS TO JOKES (FAST VERSION)
# -------------------------

def assign(jokes, tag_map):
    with conn() as c:
        with c.cursor() as cur:

            for jid, text in tqdm(jokes, desc="assign"):

                text = text.lower()

                for tag_id, phrases in tag_map.items():

                    for p in phrases:
                        if p in text:
                            cur.execute("""
                                INSERT INTO joke_tags (joke_id, tag_id)
                                VALUES (%s, %s)
                                ON CONFLICT DO NOTHING
                            """, (jid, tag_id))
                            break

        c.commit()


# -------------------------
# PIPELINE
# -------------------------

def run():
    jokes = load_jokes(limit=10000)  # тест сначала

    print("1. vocab build")
    vocab = build_vocab(jokes)

    print("2. clustering")
    clusters = cluster_vocab(vocab)

    print("clusters:", len(clusters))

    print("3. save tags")
    tag_map = save_tags(clusters)

    print("4. assign jokes")
    assign(jokes, tag_map)

    print("DONE")


if __name__ == "__main__":
    run()