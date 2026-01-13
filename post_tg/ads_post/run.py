# post_tg/run.py
import asyncio
from .sync import run_ads_sync

if __name__ == "__main__":
    asyncio.run(run_ads_sync())

