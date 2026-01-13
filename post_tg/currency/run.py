import asyncio
from .currency_post import run_currency_service

if __name__ == "__main__":
    asyncio.run(run_currency_service())
