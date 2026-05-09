import psycopg2
from collections import Counter
from tqdm import tqdm

import torch
from sentence_transformers import SentenceTransformer
from sklearn.cluster import AgglomerativeClustering
from sklearn.metrics.pairwise import cosine_similarity
from natasha import Segmenter, NewsEmbedding, NewsMorphTagger, Doc

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

# -------------------------
# NATASHA INIT
# -------------------------

segmenter = Segmenter()
emb = NewsEmbedding()
morph_tagger = NewsMorphTagger(emb)

# -------------------------
# STOPWORDS
# -------------------------

STOPWORDS = {
    "это","как","что","в","на","и","а","но","за","его","ее","бы","же",
    "ли","уже","он","она","они","мы","вы","я","к","у","с","по","из"
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
# NLP EXTRACTION (Natasha)
# -------------------------

def extract_candidates(text):
    doc = Doc(text)
    doc.segment(segmenter)
    doc.tag_morph(morph_tagger)

    tokens = []

    for t in doc.tokens:
        if t.pos in {"NOUN", "PROPN"}:
            tokens.append(t.text.lower())

    return tokens

# -------------------------
# CLEANING
# -------------------------

def clean(items):
    out = []

    for x in items:
        if not x:
            continue

        x = x.strip().lower()

        if len(x) < 3:
            continue

        if any(w in STOPWORDS for w in x.split()):
            continue

        if x.isdigit():
            continue

        out.append(x)

    return out

# -------------------------
# VOCAB BUILD
# -------------------------

def build_vocab(jokes):
    counter = Counter()

    for _, text in tqdm(jokes, desc="extracting"):

        candidates = extract_candidates(text)
        candidates = clean(candidates)

        counter.update(candidates)

    vocab = [w for w, c in counter.items() if c >= MIN_FREQ]

    print("raw vocab:", len(vocab))

    return vocab

# -------------------------
# CLUSTERING
# -------------------------

def merge_tags(vocab, model, threshold=0.75):
    emb = model.encode(vocab, normalize_embeddings=True)

    sim_matrix = np.matmul(emb, emb.T)

    used = set()
    clusters = []

    for i in range(len(vocab)):
        if i in used:
            continue

        cluster = [vocab[i]]
        used.add(i)

        for j in range(i+1, len(vocab)):
            if j in used:
                continue

            if sim_matrix[i][j] > threshold:
                cluster.append(vocab[j])
                used.add(j)

        clusters.append(cluster)

    return clusters

# -------------------------
# TAG SELECTION
# -------------------------

def pick_tag(words):
    return max(words, key=lambda w: (len(w), -w.count(" ")))

# -------------------------
# SAVE TAGS
# -------------------------

def save_tags(clusters):
    tag_map = {}

    with conn() as c:
        with c.cursor() as cur:

            for words in clusters:

                if len(words) < 1:
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
# ASSIGN TAGS
# -------------------------

def assign(jokes, tag_map):
    with conn() as c:
        with c.cursor() as cur:

            for jid, text in tqdm(jokes, desc="assign"):

                t = text.lower()

                for tag_id, phrases in tag_map.items():

                    if any(p in t for p in phrases):
                        cur.execute("""
                            INSERT INTO joke_tags (joke_id, tag_id)
                            VALUES (%s, %s)
                            ON CONFLICT DO NOTHING
                        """, (jid, tag_id))

        c.commit()

# -------------------------
# PIPELINE
# -------------------------

def run():
    jokes = load_jokes(limit=10000)

    print("1. vocab build")
    vocab = build_vocab(jokes)

    print("2. clustering")
    clusters = merge_tags(vocab, model)

    print("clusters:", len(clusters))

    print("3. save tags")
    tag_map = save_tags(clusters)

    print("4. assign jokes")
    assign(jokes, tag_map)

    print("DONE")


if __name__ == "__main__":
    run()