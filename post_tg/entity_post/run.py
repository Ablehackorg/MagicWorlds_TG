import asyncio, logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
from .sync import run_sync

if __name__ == "__main__":
    asyncio.run(run_sync())

