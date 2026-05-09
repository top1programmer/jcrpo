#db.py
import psycopg2
import bcrypt
from sqlalchemy import create_engine, desc, func
from sqlalchemy.orm import sessionmaker
from src.models import *


DATABASE_URL = "postgresql+psycopg2://postgres:postgres@localhost:5433/jokes"

engine = create_engine(
    DATABASE_URL,
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True
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
        query = query.filter(Joke.text.ilike(f"%{search}%"))

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
    password_hash = bcrypt.hashpw(
        password.encode(),
        bcrypt.gensalt()
    ).decode()

    user = User(
        username=username,
        password_hash=password_hash,
        role="user"
    )

    db.add(user)
    db.commit()
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
        text=text,
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
        joke.text = text
        db.commit()

def delete_joke_db(db, joke_id):
    db.query(Joke).filter(Joke.id == joke_id).delete()
    db.commit()

def get_or_create_tag(db: Session, name: str):
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

def get_random_joke(db: Session):
    return db.query(Joke).order_by(func.random()).first()


def get_random_joke_by_tag(db: Session, tag_name: str):
    return (
        db.query(Joke)
        .join(Joke.tags)
        .filter(Tag.name.ilike(f"%{tag_name}%"))
        .order_by(func.random())
        .first()
    )