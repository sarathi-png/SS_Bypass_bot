import asyncio
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("aiohttp").setLevel(logging.WARNING)
logging.getLogger("curl_cffi").setLevel(logging.WARNING)

from src.domain_db.db import DomainDB
from src.domain_db.checker import DomainChecker
from src.domain_db.updater import DomainUpdater
from src.bypass.engine import BypassEngine
from config import config

ARROW = "->"


async def main():
    db_path = "data/test_bypass.db"
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    db = DomainDB(db_path)
    updater = DomainUpdater(db, refresh_days=1)
    await updater.refresh(force=True)
    checker = DomainChecker(db)
    engine = BypassEngine(db, checker)

    test_urls = [
        ("1", "https://softurl.in/DIH6If0W"),
        ("2", "https://shortxlinks.in/ul2XI"),
        ("3", "https://vplink.in/1ec1"),
        ("4", "https://softurl.in/jtkHWG1W"),
    ]

    for label, url in test_urls:
        print(f"\n--- Test #{label}: {url} ---")
        start = time.time()
        try:
            result = await engine.bypass(url)
        except Exception as e:
            print(f"  EXCEPTION after {time.time()-start:.1f}s: {e}")
            import traceback
            traceback.print_exc()
            continue
        elapsed = time.time() - start
        status = "OK" if result.success else "FAIL"
        method = result.method or "none"
        final = result.final_url or ""
        error = result.error or ""
        print(f"  [{status}] method={method} time={elapsed:.1f}s")
        if final:
            print(f"  {ARROW} {final}")
        if error:
            print(f"  X {error}")

    await engine.close()


if __name__ == "__main__":
    asyncio.run(main())
