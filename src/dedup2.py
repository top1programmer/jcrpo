import psycopg2
import numpy as np
from collections import defaultdict
import ast
import numpy as np

def parse_embedding(emb):
    if emb is None:
        return None

    if isinstance(emb, list):
        return np.array(emb, dtype=np.float32)

    if isinstance(emb, str):
        return np.array(ast.literal_eval(emb), dtype=np.float32)

    return None


DB_CONFIG = {
    "dbname": "jokes",
    "user": "postgres",
    "password": "postgres",
    "host": "localhost",
    "port": 5433
}


# ----------------------------
# DB
# ----------------------------

def get_conn():
    return psycopg2.connect(**DB_CONFIG)


# ----------------------------
# cosine similarity
# ----------------------------

def cosine(a, b):
    return np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b))


# ----------------------------
# DSU (Union-Find)
# ----------------------------

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


# ----------------------------
# candidate search (trigram + vector)
# ----------------------------

def find_candidates(conn, text, emb):
    cur = conn.cursor()

    cur.execute("""
        SELECT id, text, embedding
        FROM jokes
        ORDER BY similarity(text, %s::text) DESC
        LIMIT 50
    """, (text,))

    trigram_candidates = cur.fetchall()

    cur.execute("""
        SELECT id, text, embedding
        FROM jokes
        WHERE embedding IS NOT NULL
        ORDER BY embedding <=> %s::vector
        LIMIT 50
    """, (emb.tolist(),))

    vector_candidates = cur.fetchall()

    cur.close()

    return {c[0]: c for c in trigram_candidates + vector_candidates}

# ----------------------------
# build duplicate pairs
# ----------------------------

def detect_duplicates():
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("SELECT id, text, embedding FROM jokes WHERE embedding IS NOT NULL")
    jokes = cur.fetchall()

    print(f"Loaded: {len(jokes)}")

    duplicates = []

    for jid, text, emb in jokes:

        vec = parse_embedding(emb)

        candidates = find_candidates(conn, text, vec)

        for id2, text2, emb2 in candidates.values():

            if id2 == jid or emb2 is None:
                continue

            vec2 = parse_embedding(emb2)

            if vec2 is None:
                continue

            sim = cosine(vec, vec2)

            if sim > 0.88:
                duplicates.append((jid, id2, sim))

    conn.close()

    return duplicates


# ----------------------------
# build clusters
# ----------------------------

def build_clusters(dups):
    dsu = DSU()

    for a, b, _ in dups:
        dsu.union(a, b)

    clusters = defaultdict(list)

    for a, b, _ in dups:
        root = dsu.find(a)
        clusters[root].append(a)
        clusters[root].append(b)

    # unique ids in cluster
    return [list(set(v)) for v in clusters.values()]


# ----------------------------
# choose canonical
# ----------------------------

def choose_canonical(conn, cluster):
    cur = conn.cursor()

    # берем самый “лучший” анекдот
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


# ----------------------------
# save results
# ----------------------------

def save_clusters(clusters):
    conn = get_conn()
    cur = conn.cursor()

    cluster_id = 1

    for cluster in clusters:

        canonical = choose_canonical(conn, cluster)

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


# ----------------------------
# main pipeline
# ----------------------------

def main():
    dups = detect_duplicates()

    print(f"Duplicate pairs: {len(dups)}")

    clusters = build_clusters(dups)

    print(f"Clusters: {len(clusters)}")

    conn = get_conn()

    save_clusters(clusters)

    conn.close()

    print("DONE ✅")


if __name__ == "__main__":
    main()