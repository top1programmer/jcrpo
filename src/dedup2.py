import ast
from collections import defaultdict
from datetime import datetime
from time import perf_counter

import numpy as np
import psycopg2

try:
    from src.settings import DB_CONFIG
except ModuleNotFoundError:
    from settings import DB_CONFIG


SIM_THRESHOLD = 0.88
PROGRESS_STEP = 10
USE_TRIGRAM_CANDIDATES = False
TRIGRAM_THRESHOLD = 0.2


def parse_embedding(emb):
    if emb is None:
        return None

    if isinstance(emb, list):
        return np.array(emb, dtype=np.float32)

    if isinstance(emb, str):
        return np.array(ast.literal_eval(emb), dtype=np.float32)

    return None


def get_conn():
    return psycopg2.connect(**DB_CONFIG)


def log(message):
    now = datetime.now().strftime("%H:%M:%S")
    print(f"[{now}] {message}", flush=True)


def cosine(a, b):
    denom = np.linalg.norm(a) * np.linalg.norm(b)
    if denom == 0:
        return 0
    return np.dot(a, b) / denom


class DSU:
    def __init__(self):
        self.parent = {}

    def find(self, x):
        if x not in self.parent:
            self.parent[x] = x
        if self.parent[x] != x:
            self.parent[x] = self.find(self.parent[x])
        return self.parent[x]

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


def find_candidates(conn, joke_id, text, emb, verbose=False):
    cur = conn.cursor()
    trigram_candidates = []

    if USE_TRIGRAM_CANDIDATES:
        if verbose:
            log(f"  Joke {joke_id}: trigram candidate query started")

        started_at = perf_counter()
        cur.execute(f"SET LOCAL pg_trgm.similarity_threshold = {TRIGRAM_THRESHOLD}")
        cur.execute("""
            SELECT id, text, embedding
            FROM jokes
            WHERE id > %s
              AND text %% %s::text
            ORDER BY similarity(text, %s::text) DESC
            LIMIT 50
        """, (joke_id, text, text))

        trigram_candidates = cur.fetchall()
        trigram_seconds = perf_counter() - started_at

        if verbose:
            log(
                f"  Joke {joke_id}: trigram candidates={len(trigram_candidates)} "
                f"in {trigram_seconds:.2f}s"
            )
    elif verbose:
        log(f"  Joke {joke_id}: trigram candidate query skipped")

    if verbose:
        log(f"  Joke {joke_id}: vector candidate query started")

    started_at = perf_counter()
    cur.execute("""
        SELECT id, text, embedding
        FROM jokes
        WHERE embedding IS NOT NULL AND id > %s
        ORDER BY embedding <=> %s::vector
        LIMIT 50
    """, (joke_id, emb.tolist()))

    vector_candidates = cur.fetchall()
    vector_seconds = perf_counter() - started_at

    if verbose:
        log(
            f"  Joke {joke_id}: vector candidates={len(vector_candidates)} "
            f"in {vector_seconds:.2f}s"
        )

    cur.close()

    return {c[0]: c for c in trigram_candidates + vector_candidates}


def detect_duplicates():
    log("Connecting to database")
    conn = get_conn()
    cur = conn.cursor()

    log("Loading jokes with embeddings")
    cur.execute("SELECT id, text, embedding FROM jokes WHERE embedding IS NOT NULL ORDER BY id")
    jokes = cur.fetchall()
    cur.close()

    total = len(jokes)
    log(f"Loaded jokes with embeddings: {total}")
    log(f"Similarity threshold: {SIM_THRESHOLD}")
    log(f"Trigram candidates enabled: {USE_TRIGRAM_CANDIDATES}")

    duplicates = []
    skipped = 0
    checked_candidates = 0

    for index, (jid, text, emb) in enumerate(jokes, start=1):
        verbose = index <= 5 or index % PROGRESS_STEP == 0

        if verbose:
            log(f"Processing joke {index}/{total}: id={jid}")

        vec = parse_embedding(emb)

        if vec is None:
            skipped += 1
            if verbose:
                log(f"  Joke {jid}: skipped because embedding could not be parsed")
            continue

        candidates = find_candidates(conn, jid, text, vec, verbose=verbose)
        checked_candidates += len(candidates)

        for id2, text2, emb2 in candidates.values():
            if emb2 is None:
                continue

            vec2 = parse_embedding(emb2)

            if vec2 is None:
                continue

            sim = cosine(vec, vec2)

            if sim > SIM_THRESHOLD:
                duplicates.append((jid, id2, sim))
                log(
                    f"Duplicate candidate: {jid} ~ {id2}, "
                    f"similarity={sim:.3f}, total_pairs={len(duplicates)}"
                )

        if index % PROGRESS_STEP == 0 or index == total:
            percent = (index / total * 100) if total else 100
            log(
                f"Processed {index}/{total} ({percent:.1f}%). "
                f"Candidates checked: {checked_candidates}. "
                f"Duplicate pairs: {len(duplicates)}. "
                f"Skipped embeddings: {skipped}."
            )

    conn.close()
    log("Duplicate detection finished")
    return duplicates


def build_clusters(dups):
    log("Building duplicate clusters")
    dsu = DSU()

    for a, b, _ in dups:
        dsu.union(a, b)

    clusters = defaultdict(list)

    for a, b, _ in dups:
        root = dsu.find(a)
        clusters[root].append(a)
        clusters[root].append(b)

    result = [list(set(v)) for v in clusters.values()]
    log(f"Built clusters: {len(result)}")
    return result


def choose_canonical(conn, cluster):
    cur = conn.cursor()

    cur.execute("""
        SELECT id
        FROM jokes
        WHERE id = ANY(%s)
        ORDER BY COALESCE(avg_rating, 0) DESC, id ASC
        LIMIT 1
    """, (cluster,))

    canonical = cur.fetchone()[0]
    cur.close()

    return canonical


def save_clusters(clusters):
    log("Clearing previous duplicate marks")
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        UPDATE jokes
        SET canonical_id = NULL,
            duplicate_cluster_id = NULL
        WHERE canonical_id IS NOT NULL
           OR duplicate_cluster_id IS NOT NULL
    """)
    log(f"Cleared rows: {cur.rowcount}")

    if not clusters:
        log("No clusters to save")
        conn.commit()
        cur.close()
        conn.close()
        return

    log("Saving clusters to database")
    cluster_id = 1

    for cluster in clusters:
        canonical = choose_canonical(conn, cluster)
        log(
            f"Saving cluster {cluster_id}/{len(clusters)}: "
            f"canonical_id={canonical}, jokes={len(cluster)}"
        )

        for joke_id in cluster:
            cur.execute("""
                UPDATE jokes
                SET canonical_id = %s,
                    duplicate_cluster_id = %s
                WHERE id = %s
            """, (canonical, cluster_id, joke_id))

        cluster_id += 1

    conn.commit()
    cur.close()
    conn.close()
    log("Clusters saved")


def main():
    log("Starting duplicate search")
    dups = detect_duplicates()

    log(f"Duplicate pairs found: {len(dups)}")

    clusters = build_clusters(dups)

    log(f"Clusters ready to save: {len(clusters)}")

    save_clusters(clusters)

    log("DONE")


if __name__ == "__main__":
    main()
