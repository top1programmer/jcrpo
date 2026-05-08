import time
import argparse
from datetime import datetime, timedelta

from parser_core import init_db, parse_day, save_jokes

REQUEST_DELAY = 1


def parse_range(start_date, end_date):
    current = start_date
    
    while current <= end_date:
        date_str = current.strftime("%Y-%m-%d")

        jokes = parse_day(date_str)
        save_jokes(jokes)

        time.sleep(REQUEST_DELAY)
        current += timedelta(days=1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)

    args = parser.parse_args()
    
    start = datetime.strptime(args.start, "%Y-%m-%d").date()
    end = datetime.strptime(args.end, "%Y-%m-%d").date()

    init_db()
    parse_range(start, end)