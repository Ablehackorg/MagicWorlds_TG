# run.py
import asyncio
from .second_subscribers_booster import run_second_subscribers_checker

if __name__ == "__main__":
    asyncio.run(run_second_subscribers_checker())
