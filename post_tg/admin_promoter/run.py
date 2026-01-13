# admin_promoter/run.py

import asyncio
from .admin_promoter import run_admin_promoter

if __name__ == "__main__":
    asyncio.run(run_admin_promoter())

