from collections import defaultdict
from datetime import datetime
import re
from time import perf_counter

import numpy as np
import psycopg2
import torch
from natasha import Doc, MorphVocab, NewsEmbedding, NewsMorphTagger, Segmenter
from psycopg2.extras import execute_values
from sentence_transformers import SentenceTransformer
from sklearn.neighbors import NearestNeighbors
from tqdm import tqdm

try:
    from src.settings import DB_CONFIG
except ModuleNotFoundError:
    from settings import DB_CONFIG


MIN_FREQ = 5
SIM_THRESHOLD = 0.88
BATCH_SIZE = 5000
EMBED_BATCH_SIZE = 256
MAX_VOCAB_SIZE = 30000
MAX_RAW_WORDS = 80000

ALLOWED_POS = {"NOUN", "PROPN", "ADJ"}
CYRILLIC_WORD_RE = re.compile(r"^[а-яё]+(?:-[а-яё]+)?$")
WORD_TO_LEMMA = {}
TAG_ALIASES = {}

device = "cuda" if torch.cuda.is_available() else "cpu"
print("Device:", device)

model = SentenceTransformer(
    "paraphrase-multilingual-MiniLM-L12-v2",
    device=device
)

segmenter = Segmenter()
natasha_emb = NewsEmbedding()
morph_tagger = NewsMorphTagger(natasha_emb)
morph_vocab = MorphVocab()

STOPWORDS = {
    "это", "как", "что", "в", "на", "и", "а", "но", "за", "его", "ее",
    "бы", "же", "ли", "уже", "он", "она", "они", "мы", "вы", "я",
    "к", "у", "с", "по", "из", "под", "над", "для", "от", "до",
    "не", "нет", "да", "ну", "вот", "там", "тут", "так", "кто", "где",
}


def log(message):
    now = datetime.now().strftime("%H:%M:%S")
    print(f"[{now}] {message}", flush=True)


def conn():
    return psycopg2.connect(**DB_CONFIG)


def count_pending_jokes():
    with conn() as c:
        with c.cursor() as cur:
            cur.execute("""
                SELECT count(*)
                FROM jokes
                WHERE text IS NOT NULL
                  AND COALESCE(tags_generated, FALSE) = FALSE
            """)
            return cur.fetchone()[0]


def stream_jokes(batch_size=BATCH_SIZE):
    c = conn()
    cur = c.cursor(name="jokes_cursor")
    cur.itersize = batch_size

    cur.execute("""
        SELECT id, text
        FROM jokes
        WHERE text IS NOT NULL
          AND COALESCE(tags_generated, FALSE) = FALSE
        ORDER BY id
    """)

    while True:
        rows = cur.fetchmany(batch_size)

        if not rows:
            break

        yield rows

    cur.close()
    c.close()


def normalize_candidate_word(word):
    if not word:
        return None

    word = word.lower()

    if not CYRILLIC_WORD_RE.match(word):
        return None

    if word in STOPWORDS:
        return None

    try:
        doc = Doc(word)
        doc.segment(segmenter)
        doc.tag_morph(morph_tagger)

        for token in doc.tokens:
            if token.pos not in ALLOWED_POS:
                return None

            token.lemmatize(morph_vocab)
            lemma = (token.lemma or token.text).lower()

            if len(lemma) < 3:
                return None

            if lemma in STOPWORDS:
                return None

            if not CYRILLIC_WORD_RE.match(lemma):
                return None

            return lemma
    except Exception:
        return None

    return None


def build_vocab():
    started_at = perf_counter()
    total = count_pending_jokes()

    log(f"Pending jokes for tag generation: {total}")
    log("Building readable vocabulary in PostgreSQL")

    if total == 0:
        return []

    with conn() as c:
        with c.cursor() as cur:
            cur.execute("""
                SELECT word, count(*) AS nentry
                FROM (
                    SELECT regexp_split_to_table(lower(j.text), '[^а-яё-]+') AS word
                    FROM jokes j
                    WHERE j.text IS NOT NULL
                      AND COALESCE(j.tags_generated, FALSE) = FALSE
                ) words
                WHERE length(word) >= 3
                  AND word ~ '^[а-яё]+(-[а-яё]+)?$'
                  AND word NOT LIKE 'www%%'
                GROUP BY word
                HAVING count(*) >= %s
                ORDER BY count(*) DESC
                LIMIT %s
            """, (MIN_FREQ, MAX_RAW_WORDS))
            rows = cur.fetchall()

    lemma_counts = defaultdict(int)
    word_to_lemma = {}
    skipped = 0

    for word, count in tqdm(rows, desc="normalizing words"):
        word = word.lower()
        lemma = normalize_candidate_word(word)

        if not lemma:
            skipped += 1
            continue

        lemma_counts[lemma] += count
        word_to_lemma[word] = lemma

    vocab = [
        lemma
        for lemma, count in sorted(
            lemma_counts.items(),
            key=lambda item: item[1],
            reverse=True
        )
        if count >= MIN_FREQ
    ][:MAX_VOCAB_SIZE]

    selected = set(vocab)
    WORD_TO_LEMMA.clear()
    WORD_TO_LEMMA.update({
        word: lemma
        for word, lemma in word_to_lemma.items()
        if lemma in selected
    })

    log(
        f"VOCAB: {len(vocab)} lemmas from {len(rows)} raw words, "
        f"aliases: {len(WORD_TO_LEMMA)}, skipped: {skipped}, "
        f"in {perf_counter() - started_at:.1f}s"
    )
    return vocab


class DSU:
    def __init__(self, size):
        self.parent = list(range(size))

    def find(self, x):
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a, b):
        ra = self.find(a)
        rb = self.find(b)

        if ra != rb:
            self.parent[rb] = ra


def merge_tags(vocab):
    if not vocab:
        return []

    if len(vocab) == 1:
        return [vocab]

    log("Embedding tag vocabulary")
    emb = model.encode(
        vocab,
        normalize_embeddings=True,
        batch_size=EMBED_BATCH_SIZE,
        show_progress_bar=True
    )
    emb = np.array(emb)

    log("Searching similar tags without full similarity matrix")
    radius = 1 - SIM_THRESHOLD
    neighbors = NearestNeighbors(
        metric="cosine",
        radius=radius,
        algorithm="brute",
        n_jobs=-1
    )
    neighbors.fit(emb)

    indices = neighbors.radius_neighbors(emb, return_distance=False)
    dsu = DSU(len(vocab))

    for i, near_ids in enumerate(tqdm(indices, desc="clustering")):
        for j in near_ids:
            if i == j:
                continue

            if len(vocab[i]) <= 8 or len(vocab[j]) <= 8:
                continue

            dsu.union(i, int(j))

    by_root = defaultdict(list)
    for i, word in enumerate(vocab):
        by_root[dsu.find(i)].append(word)

    clusters = list(by_root.values())
    lemma_to_tag = {}

    for cluster in clusters:
        main_tag = pick_tag(cluster)

        for lemma in cluster:
            lemma_to_tag[lemma] = main_tag

    TAG_ALIASES.clear()
    TAG_ALIASES.update({
        word: lemma_to_tag[lemma]
        for word, lemma in WORD_TO_LEMMA.items()
        if lemma in lemma_to_tag
    })

    log(f"clusters: {len(clusters)}")
    log(f"tag aliases: {len(TAG_ALIASES)}")
    return clusters


def pick_tag(words):
    words = sorted(
        words,
        key=lambda x: (
            -len(x),
            x.count("-"),
            x.count(" ")
        )
    )

    return words[0]


def save_tags(clusters):
    if not clusters:
        log("No tags to save")
        return

    log("Preparing tag embeddings")
    main_tags = [pick_tag(cluster) for cluster in clusters]
    embeddings = model.encode(
        main_tags,
        normalize_embeddings=True,
        batch_size=EMBED_BATCH_SIZE,
        show_progress_bar=True
    )

    rows = [
        (tag, embedding.tolist())
        for tag, embedding in zip(main_tags, embeddings)
    ]

    log(f"Saving tags: {len(rows)}")

    with conn() as c:
        with c.cursor() as cur:
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


def assign_tags_sql():
    log("Assigning tags in postgres")

    with conn() as c:
        with c.cursor() as cur:
            if TAG_ALIASES:
                log(f"Loading tag aliases into temporary table: {len(TAG_ALIASES)}")
                cur.execute("""
                    CREATE TEMP TABLE tmp_tag_aliases (
                        word text PRIMARY KEY,
                        tag_name varchar(100) NOT NULL
                    ) ON COMMIT DROP
                """)
                execute_values(
                    cur,
                    """
                    INSERT INTO tmp_tag_aliases(word, tag_name)
                    VALUES %s
                    ON CONFLICT(word) DO UPDATE
                    SET tag_name = EXCLUDED.tag_name
                    """,
                    list(TAG_ALIASES.items())
                )

                cur.execute("""
                    INSERT INTO joke_tags (joke_id, tag_id)
                    SELECT DISTINCT
                        words.joke_id,
                        t.id
                    FROM (
                        SELECT
                            j.id AS joke_id,
                            regexp_split_to_table(lower(j.text), '[^а-яё-]+') AS word
                        FROM jokes j
                        WHERE j.text IS NOT NULL
                          AND COALESCE(j.tags_generated, FALSE) = FALSE
                    ) words
                    JOIN tmp_tag_aliases a
                        ON a.word = words.word
                    JOIN tags t
                        ON t.name = a.tag_name
                    ON CONFLICT DO NOTHING
                """)
            else:
                cur.execute("""
                    INSERT INTO joke_tags (joke_id, tag_id)
                    SELECT DISTINCT
                        words.joke_id,
                        t.id
                    FROM (
                        SELECT
                            j.id AS joke_id,
                            regexp_split_to_table(lower(j.text), '[^а-яё-]+') AS word
                        FROM jokes j
                        WHERE j.text IS NOT NULL
                          AND COALESCE(j.tags_generated, FALSE) = FALSE
                    ) words
                    JOIN tags t
                        ON t.name = words.word
                    WHERE length(words.word) >= 3
                      AND words.word ~ '^[а-яё]+(-[а-яё]+)?$'
                    ON CONFLICT DO NOTHING
                """)
            log(f"Assigned joke_tags rows: {cur.rowcount}")

            cur.execute("""
                UPDATE jokes
                SET tags_generated = TRUE
                WHERE text IS NOT NULL
                  AND COALESCE(tags_generated, FALSE) = FALSE
            """)
            log(f"Marked jokes as processed: {cur.rowcount}")

        c.commit()


def show_similar_tags(limit=100):
    with conn() as c:
        with c.cursor() as cur:
            cur.execute("""
                SELECT
                    t1.name,
                    t2.name,
                    1 - (t1.embedding <=> t2.embedding) AS sim
                FROM tags t1
                JOIN tags t2
                    ON t1.id < t2.id
                WHERE
                    1 - (t1.embedding <=> t2.embedding) > %s
                ORDER BY sim DESC
                LIMIT %s
            """, (SIM_THRESHOLD, limit))

            rows = cur.fetchall()

            for row in rows:
                print(row)


def run():
    started_at = perf_counter()

    log("1. build vocab")
    vocab = build_vocab()

    log("2. merge semantic duplicates")
    clusters = merge_tags(vocab)

    log("3. save tags")
    save_tags(clusters)

    log("4. postgres assign")
    assign_tags_sql()

    log(f"DONE in {perf_counter() - started_at:.1f}s")


if __name__ == "__main__":
    run()
