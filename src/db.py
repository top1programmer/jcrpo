import random

import bcrypt
import psycopg2
from sqlalchemy import create_engine, desc, func, text as sql_text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from src.models import Joke, Tag, User
from src.settings import DATABASE_URL, DB_CONFIG


engine = create_engine(
    DATABASE_URL,
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,
    future=True,
)

Session = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False
)


def get_db():
    db = Session()
    try:
        yield db
    finally:
        db.close()


def get_conn():
    return psycopg2.connect(**DB_CONFIG)


def fetch_jokes(db, limit, offset, date=None, tag=None, search=None, sort=None):
    query = db.query(Joke)

    if date:
        query = query.filter(Joke.source_date == date)

    if search:
        query = query.filter(
            sql_text("to_tsvector('russian', jokes.text) @@ plainto_tsquery('russian', :search)")
        ).params(search=search)

    if tag:
        query = query.join(Joke.tags).filter(Tag.name.ilike(f"%{tag}%"))

    if sort == "rating":
        query = query.order_by(desc(Joke.avg_rating))
    elif sort == "date":
        query = query.order_by(desc(Joke.source_date))
    elif sort == "random":
        query = query.order_by(func.random())
    elif sort == "new":
        query = query.order_by(desc(Joke.created_at))
    else:
        query = query.order_by(desc(Joke.id))

    return query.offset(offset).limit(limit).all()


def create_user(db: Session, username: str, password: str):
    username = username.strip()
    password_hash = bcrypt.hashpw(
        password.encode(),
        bcrypt.gensalt()
    ).decode()

    user = User(
        username=username,
        password_hash=password_hash,
        role="user"
    )

    try:
        db.add(user)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise

    db.refresh(user)
    return user.id


def get_user_by_username(db: Session, username: str):
    return db.query(User).filter(User.username == username).first()


def verify_user(db: Session, username: str, password: str):
    user = db.query(User).filter(User.username == username).first()

    if not user:
        return None

    if bcrypt.checkpw(password.encode(), user.password_hash.encode()):
        return {
            "id": user.id,
            "username": user.username,
            "role": user.role
        }

    return None


def create_joke(db: Session, text: str, author_id: int):
    joke = Joke(
        text=text.strip(),
        author_id=author_id,
        avg_rating=0,
        ratings_count=0
    )

    db.add(joke)
    db.commit()
    db.refresh(joke)

    return joke


def get_joke(db, joke_id):
    return db.query(Joke).filter(Joke.id == joke_id).first()


def update_joke(db, joke_id, text):
    joke = db.query(Joke).filter(Joke.id == joke_id).first()

    if joke:
        joke.text = text.strip()
        joke.tags_generated = False
        db.commit()


def delete_joke_db(db, joke_id):
    db.query(Joke).filter(Joke.id == joke_id).delete()
    db.commit()


def fetch_duplicate_groups(db: Session):
    jokes = (
        db.query(Joke)
        .filter(Joke.duplicate_cluster_id.isnot(None))
        .order_by(Joke.duplicate_cluster_id, Joke.id)
        .all()
    )

    groups = []
    current_group = None

    for joke in jokes:
        if not current_group or current_group["id"] != joke.duplicate_cluster_id:
            current_group = {
                "id": joke.duplicate_cluster_id,
                "canonical_id": joke.canonical_id,
                "jokes": []
            }
            groups.append(current_group)

        current_group["jokes"].append(joke)

    return groups


def resolve_duplicate_group(db: Session, cluster_id: int, keep_joke_id: int):
    group_jokes = (
        db.query(Joke)
        .filter(Joke.duplicate_cluster_id == cluster_id)
        .all()
    )

    keep_exists = any(joke.id == keep_joke_id for joke in group_jokes)

    if not group_jokes or not keep_exists:
        return 0

    delete_ids = [
        joke.id
        for joke in group_jokes
        if joke.id != keep_joke_id
    ]

    if delete_ids:
        db.query(Joke).filter(Joke.id.in_(delete_ids)).delete(synchronize_session=False)

    kept_joke = db.query(Joke).filter(Joke.id == keep_joke_id).first()

    if kept_joke:
        kept_joke.canonical_id = None
        kept_joke.duplicate_cluster_id = None

    db.commit()
    return len(delete_ids)


def get_or_create_tag(db: Session, name: str):
    name = name.strip().lower()
    tag = db.query(Tag).filter(Tag.name == name).first()

    if tag:
        return tag

    tag = Tag(name=name)
    db.add(tag)
    db.commit()
    db.refresh(tag)

    return tag


def add_tag_to_joke(db: Session, joke_id: int, tag_name: str):
    joke = db.query(Joke).filter(Joke.id == joke_id).first()
    tag = get_or_create_tag(db, tag_name)

    if joke and tag and tag not in joke.tags:
        joke.tags.append(tag)
        db.commit()


def get_random_joke(db: Session, tag_name: str = None):
    query = db.query(Joke)

    if tag_name:
        query = query.join(Joke.tags).filter(Tag.name.ilike(f"%{tag_name}%"))

    max_id = query.with_entities(func.max(Joke.id)).scalar()

    if not max_id:
        return None

    for _ in range(10):
        candidate_id = random.randint(1, max_id)
        joke = (
            query
            .filter(Joke.id >= candidate_id)
            .order_by(Joke.id)
            .first()
        )

        if joke:
            return joke

    return query.order_by(desc(Joke.id)).first()


def get_random_joke_by_tag(db: Session, tag_name: str):
    return get_random_joke(db, tag_name)
