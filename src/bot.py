from aiogram import Bot, Dispatcher
from aiogram.filters import Command
from aiogram.types import Message

from src.db import Session
from src.db import get_random_joke as fetch_random_joke
from src.settings import BOT_TOKEN


dp = Dispatcher()


def get_random_joke(tag=None):
    db = Session()

    try:
        joke = fetch_random_joke(db, tag)

        if not joke:
            return "Анекдот не найден"

        return joke.text
    finally:
        db.close()


@dp.message(Command("start"))
async def start(message: Message):
    await message.answer(
        "Команды:\n"
        "/random - случайный анекдот\n\n"
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
    if not BOT_TOKEN:
        print("BOT_TOKEN is not configured; Telegram bot is disabled")
        return

    bot = Bot(token=BOT_TOKEN)
    print("Bot started")
    await dp.start_polling(bot)
