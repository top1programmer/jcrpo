#db.py
import psycopg2
import bcrypt

DB_CONFIG = {
    "dbname": "jokes",
    "user": "postgres",
    "password": "postgres",
    "host": "localhost",
    "port": 5433
}


def get_conn():
    return psycopg2.connect(**DB_CONFIG)


def fetch_jokes(limit, offset, date=None, tag=None, search=None, sort=None):
    conn = get_conn()
    cur = conn.cursor()

    query = """
        SELECT
            j.id,
            j.text,
            j.source_date,
            j.final_rating,
            COALESCE((
                SELECT array_agg(t.name)
                FROM joke_tags jt
                JOIN tags t ON t.id = jt.tag_id
                WHERE jt.joke_id = j.id
            ), '{}') AS tags
        FROM jokes_with_rating j
        WHERE 1=1
    """

    params = []

    if date:
        query += " AND j.source_date = %s"
        params.append(date)

    if search:
        query += " AND j.text ILIKE %s"
        params.append(f"%{search}%")

    if tag:
        query += """
            AND EXISTS (
                SELECT 1 FROM joke_tags jt2
                JOIN tags t2 ON t2.id = jt2.tag_id
                WHERE jt2.joke_id = j.id
                AND t2.name ILIKE %s
            )
        """
        params.append(f"%{tag}%")

    # сортировка
    if sort == "rating":
        query += " ORDER BY j.final_rating DESC NULLS LAST"
    elif sort == "date":
        query += " ORDER BY j.source_date DESC NULLS LAST"
    elif sort == "random":
        query += " ORDER BY random()"
    else:
        query += " ORDER BY j.id DESC"

    query += " LIMIT %s OFFSET %s"
    params.extend([limit, offset])

    cur.execute(query, params)

    columns = [desc[0] for desc in cur.description]

    rows = [dict(zip(columns, row)) for row in cur.fetchall()]

    cur.close()
    conn.close()

    return rows

def create_user(username, password):
    conn = get_conn()
    cur = conn.cursor()

    password_hash = bcrypt.hashpw(
        password.encode(),
        bcrypt.gensalt()
    ).decode()

    cur.execute("""
        INSERT INTO users (username, password_hash, role)
        VALUES (%s, %s, 'user')
        RETURNING id
    """, (username, password_hash))

    user_id = cur.fetchone()[0]

    conn.commit()

    cur.close()
    conn.close()

    return user_id


def get_user_by_username(username):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT id, username, password_hash, role
        FROM users
        WHERE username = %s
    """, (username,))

    row = cur.fetchone()

    cur.close()
    conn.close()

    return row


def verify_user(username, password):
    user = get_user_by_username(username)

    if not user:
        return None

    user_id, username, password_hash, role = user

    if bcrypt.checkpw(password.encode(),password_hash.encode()):
        return {
            "id": user_id,
            "username": username,
            "role": role
        }

    return None