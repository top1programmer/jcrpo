from datetime import datetime, timedelta
from apscheduler.schedulers.blocking import BlockingScheduler

from parser_core import init_db, parse_day, save_jokes


def daily_job():
    yesterday = datetime.now() - timedelta(days=1)
    date_str = yesterday.strftime("%Y-%m-%d")

    print("Парсим вчера:", date_str)

    jokes = parse_day(date_str)
    save_jokes(jokes)


if __name__ == "__main__":
    init_db()

    scheduler = BlockingScheduler()
    scheduler.add_job(daily_job, 'cron', hour=3, minute=0)

    print("Daily parser started (03:00)")
    scheduler.start()