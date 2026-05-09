# src/bot.py

import asyncio
import random
import os

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message
from aiogram.filters import Command
from dotenv import load_dotenv

from sqlalchemy import func
from src.db import Session
from src.models import Joke, Tag

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


def get_random_joke(tag=None):
    db = Session()

    try:
        query = db.query(Joke)

        if tag:
            query = (
                query.join(Joke.tags)
                .filter(Tag.name.ilike(f"%{tag}%"))
            )

        joke = query.order_by(func.random()).first()

        if not joke:
            return "Анекдот не найден"

        return joke.text

    finally:
        db.close()


@dp.message(Command("start"))
async def start(message: Message):
    await message.answer(
        "Команды:\n"
        "/random — случайный анекдот\n\n"
        "Или просто отправь тег:\n"
        "кот\n"
        "работа\n"
        "программисты"
    )

@dp.message(Command("random"))
async def random_joke(message: Message):
    joke = get_random_joke()

    await message.answer(joke)


@dp.message()
async def random_by_tag(message: Message):
    tag = message.text.strip()

    joke = get_random_joke(tag)

    await message.answer(joke)


async def start_bot():
    print("Bot started")
    await dp.start_polling(bot)