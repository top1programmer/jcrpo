from sqlalchemy import Column, Integer, String, Text, Float, Date, ForeignKey, TIMESTAMP
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


class Joke(Base):
    __tablename__ = "jokes"

    id = Column(Integer, primary_key=True)
    text = Column(Text, nullable=False)
    source_id = Column(Integer, unique=True)
    source_date = Column(Date)

    source_rating = Column(Float)
    avg_rating = Column(Float, default=0)
    ratings_count = Column(Integer, default=0)

    created_at = Column(TIMESTAMP)

    tags = relationship("Tag", secondary="joke_tags", back_populates="jokes")


class Tag(Base):
    __tablename__ = "tags"

    id = Column(Integer, primary_key=True)
    name = Column(String)

    jokes = relationship("Joke", secondary="joke_tags", back_populates="tags")


class JokeTag(Base):
    __tablename__ = "joke_tags"

    joke_id = Column(Integer, ForeignKey("jokes.id"), primary_key=True)
    tag_id = Column(Integer, ForeignKey("tags.id"), primary_key=True)