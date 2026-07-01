import asyncio
import logging
from datetime import datetime, timezone, timedelta

from .db import DomainDB
from .sources import fetch_all_sources

logger = logging.getLogger(__name__)


class DomainUpdater:
    def __init__(self, db: DomainDB, refresh_days: int = 7):
        self.db = db
        self.refresh_days = refresh_days
        self._last_refresh: datetime | None = None

    def needs_refresh(self) -> bool:
        if self._last_refresh is None:
            return True
        elapsed = datetime.now(timezone.utc) - self._last_refresh
        return elapsed > timedelta(days=self.refresh_days)

    async def refresh(self, force: bool = False) -> dict:
        if not force and not self.needs_refresh():
            return {"refreshed": False, "reason": "within_ttl"}

        logger.info("Refreshing domain database from community sources...")
        try:
            domains = await fetch_all_sources()
        except Exception as e:
            logger.error(f"Failed to fetch domain sources: {e}")
            return {"refreshed": False, "error": str(e)}

        if not domains:
            logger.warning("No domains fetched from any source")
            return {"refreshed": False, "error": "empty_result"}

        self.db.set_domains_bulk(domains)
        self._last_refresh = datetime.now(timezone.utc)

        active = sum(1 for d in domains if d["status"] == "active")
        inactive = sum(1 for d in domains if d["status"] == "inactive")
        logger.info(
            f"Domain database refreshed: {active} active, {inactive} inactive "
            f"({len(domains)} total from community)"
        )
        return {
            "refreshed": True,
            "total": len(domains),
            "active": active,
            "inactive": inactive,
        }
