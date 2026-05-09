import psycopg2
from psycopg2.extras import execute_values

from collections import Counter
from tqdm import tqdm

import numpy as np
import torch

from sentence_transformers import SentenceTransformer
from sklearn.metrics.pairwise import cosine_similarity

from natasha import (
    Segmenter,
    NewsEmbedding,
    NewsMorphTagger,
    Doc,
    MorphVocab
)

# =========================================================
# CONFIG
# =========================================================

DB_CONFIG = {
    "dbname": "jokes",
    "user": "postgres",
    "password": "postgres",
    "host": "localhost",
    "port": 5433
}

MIN_FREQ = 5
SIM_THRESHOLD = 0.88
BATCH_SIZE = 5000

device = "cuda" if torch.cuda.is_available() else "cpu"
print("Device:", device)

# =========================================================
# MODEL
# =========================================================

model = SentenceTransformer(
    "paraphrase-multilingual-MiniLM-L12-v2",
    device=device
)

# =========================================================
# NATASHA
# =========================================================

segmenter = Segmenter()

natasha_emb = NewsEmbedding()

morph_tagger = NewsMorphTagger(natasha_emb)

morph_vocab = MorphVocab()

# =========================================================
# STOPWORDS
# =========================================================

STOPWORDS = {
    "это","как","что","в","на","и","а","но","за","его","ее",
    "бы","же","ли","уже","он","она","они","мы","вы","я",
    "к","у","с","по","из","под","над","для","от","до"
}

# =========================================================
# DB
# =========================================================

def conn():
    return psycopg2.connect(**DB_CONFIG)

# =========================================================
# LOAD JOKES BY BATCH
# =========================================================

def stream_jokes(batch_size=BATCH_SIZE):

    c = conn()

    cur = c.cursor(name="jokes_cursor")

    cur.itersize = batch_size

    cur.execute("""
        SELECT id, text
        FROM jokes
        WHERE text IS NOT NULL
    """)

    while True:

        rows = cur.fetchmany(batch_size)

        if not rows:
            break

        yield rows

    cur.close()
    c.close()

# =========================================================
# NORMALIZATION
# =========================================================

def normalize_word(word):

    try:
        doc = Doc(word)

        doc.segment(segmenter)
        doc.tag_morph(morph_tagger)

        for t in doc.tokens:
            t.lemmatize(morph_vocab)
            return t.lemma.lower()

    except:
        return word.lower()

    return word.lower()

# =========================================================
# ENTITY EXTRACTION
# =========================================================

def extract_candidates(text):

    try:
        doc = Doc(text)

        doc.segment(segmenter)
        doc.tag_morph(morph_tagger)

        result = []

        for t in doc.tokens:

            if t.pos not in {"NOUN", "PROPN", "ADJ"}:
                continue

            word = normalize_word(t.text)

            if len(word) < 3:
                continue

            if word in STOPWORDS:
                continue

            if word.isdigit():
                continue

            result.append(word)

        return result

    except:
        return []

# =========================================================
# BUILD VOCAB
# =========================================================

def build_vocab():

    counter = Counter()

    for batch in stream_jokes():

        for _, text in tqdm(batch, desc="extracting"):

            words = extract_candidates(text)

            counter.update(words)

    vocab = [
        word
        for word, freq in counter.items()
        if freq >= MIN_FREQ
    ]

    print("VOCAB:", len(vocab))

    return vocab

# =========================================================
# EMBEDDING CLUSTERING
# =========================================================

def merge_tags(vocab):

    print("embedding tags...")

    emb = model.encode(
        vocab,
        normalize_embeddings=True,
        batch_size=256,
        show_progress_bar=True
    )

    emb = np.array(emb)

    print("similarity matrix...")
    
    sim = cosine_similarity(emb)

    visited = set()

    clusters = []

    for i in tqdm(range(len(vocab)), desc="clustering"):

        if i in visited:
            continue

        cluster = []

        stack = [i]

        visited.add(i)

        while stack:

            node = stack.pop()

            cluster.append(vocab[node])

            for j in range(len(vocab)):

                if j in visited:
                    continue

                if len(vocab[i]) <= 8 or len(vocab[j]) <= 8:
                    continue

                if sim[node][j] >= SIM_THRESHOLD:

                    visited.add(j)

                    stack.append(j)

        clusters.append(cluster)

    print("clusters:", len(clusters))

    return clusters

# =========================================================
# PICK BEST TAG
# =========================================================

def pick_tag(words):

    # самый длинный + без мусора

    words = sorted(
        words,
        key=lambda x: (
            -len(x),
            x.count("-"),
            x.count(" ")
        )
    )

    return words[0]

# =========================================================
# SAVE TAGS
# =========================================================

def save_tags(clusters):

    print("saving tags...")

    with conn() as c:

        with c.cursor() as cur:

            rows = []

            for cluster in clusters:

                main_tag = pick_tag(cluster)

                embedding = model.encode(main_tag).tolist()

                rows.append((
                    main_tag,
                    embedding
                ))

            execute_values(
                cur,
                """
                INSERT INTO tags(name, embedding)
                VALUES %s
                ON CONFLICT(name)
                DO NOTHING
                """,
                rows
            )

        c.commit()

# =========================================================
# POSTGRES TAG MATCHING
# =========================================================

def assign_tags_sql():

    print("assigning tags in postgres...")

    with conn() as c:

        with c.cursor() as cur:

            cur.execute("""
                INSERT INTO joke_tags (joke_id, tag_id)

                SELECT
                    j.id,
                    t.id

                FROM jokes j

                JOIN tags t
                ON to_tsvector('russian', j.text)
                @@ plainto_tsquery('russian', t.name)

                ON CONFLICT DO NOTHING
            """)

        c.commit()

# =========================================================
# OPTIONAL:
# REMOVE SEMANTIC DUPLICATES INSIDE POSTGRES
# =========================================================

def show_similar_tags(limit=100):

    with conn() as c:

        with c.cursor() as cur:

            cur.execute(f"""
                SELECT
                    t1.name,
                    t2.name,
                    1 - (t1.embedding <=> t2.embedding) AS sim

                FROM tags t1
                JOIN tags t2
                    ON t1.id < t2.id

                WHERE
                    1 - (t1.embedding <=> t2.embedding)
                    > {SIM_THRESHOLD}

                ORDER BY sim DESC

                LIMIT %s
            """, (limit,))

            rows = cur.fetchall()

            for r in rows:
                print(r)

# =========================================================
# MAIN
# =========================================================

def run():

    print("1. build vocab")
    vocab = build_vocab()

    print("2. merge semantic duplicates")
    clusters = merge_tags(vocab)

    print("3. save tags")
    save_tags(clusters)

    print("4. postgres assign")
    assign_tags_sql()

    print("DONE")

# =========================================================

if __name__ == "__main__":
    run()