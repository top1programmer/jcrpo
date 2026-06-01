import psycopg2
import requests
from bs4 import BeautifulSoup

try:
    from src.settings import DB_CONFIG
except ModuleNotFoundError:
    from settings import DB_CONFIG


BASE_URL = "https://www.anekdot.ru/release/anekdot/day/{}/"
HEADERS = {"User-Agent": "JokeHub educational parser/1.0"}


def init_db():
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS jokes (
        id SERIAL PRIMARY KEY,
        text TEXT NOT NULL,
        source_id BIGINT UNIQUE,
        source_date DATE,
        source_rating FLOAT,
        avg_rating FLOAT DEFAULT 0,
        ratings_count INT DEFAULT 0,
        has_source_rating BOOLEAN DEFAULT FALSE,
        canonical_id INT,
        duplicate_cluster_id INT,
        tags_generated BOOLEAN DEFAULT FALSE,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

    conn.commit()
    cur.close()
    conn.close()


def parse_day(date_str):
    url = BASE_URL.format(date_str)
    print(f"Парсим: {date_str}")

    try:
        response = requests.get(url, headers=HEADERS, timeout=10)
        response.raise_for_status()
    except Exception as e:
        print("Ошибка запроса:", e)
        return []

    soup = BeautifulSoup(response.text, "html.parser")
    jokes = []

    for block in soup.find_all("div", class_="topicbox"):
        text_div = block.find("div", class_="text")
        if not text_div:
            continue

        text = text_div.get_text(" ", strip=True)
        source_id = block.get("data-id")

        if not text or not source_id:
            continue

        star_tag = block.find("a", class_="user-star")
        stars = star_tag.get_text().count("★") if star_tag else None
        has_rating = stars is not None
        source_rating = float(stars) if stars is not None else None

        jokes.append((text, source_id, date_str, source_rating, has_rating))

    return jokes


def save_jokes(jokes):
    if not jokes:
        return

    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()

    cur.executemany("""
        INSERT INTO jokes (text, source_id, source_date, source_rating, has_source_rating)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (source_id) DO NOTHING;
    """, jokes)

    conn.commit()
    cur.close()
    conn.close()
