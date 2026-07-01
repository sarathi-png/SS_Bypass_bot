#!/usr/bin/env python3
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.domain_db.db import DomainDB
from src.domain_db.updater import DomainUpdater


async def main():
    db = DomainDB("data/bot_cache.db")
    updater = DomainUpdater(db)
    result = await updater.refresh(force=True)
    print(f"Result: {result}")


if __name__ == "__main__":
    asyncio.run(main())
