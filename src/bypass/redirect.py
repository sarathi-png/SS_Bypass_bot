import asyncio
import logging
from typing import Optional
from urllib.parse import urlparse, urlunparse

import aiohttp

logger = logging.getLogger(__name__)

MAX_HOPS = 15
TIMEOUT = 15


def _normalize_location(base_url: str, location: str) -> str:
    if location.startswith("http://") or location.startswith("https://"):
        return location
    parsed = urlparse(base_url)
    if location.startswith("//"):
        return f"{parsed.scheme}:{location}"
    if location.startswith("/"):
        return f"{parsed.scheme}://{parsed.netloc}{location}"
    path = parsed.path.rsplit("/", 1)[0] if "/" in parsed.path else ""
    return f"{parsed.scheme}://{parsed.netloc}{path}/{location}"


class RedirectResolver:
    def __init__(self):
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            connector = aiohttp.TCPConnector(limit=10)
            timeout = aiohttp.ClientTimeout(total=TIMEOUT)
            self._session = aiohttp.ClientSession(
                connector=connector,
                timeout=timeout,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/135.0.0.0 Safari/537.36"
                    ),
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "en-US,en;q=0.9",
                },
                max_redirects=0,
            )
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    async def resolve(self, url: str, max_hops: int = MAX_HOPS) -> dict:
        if not url.startswith(("http://", "https://")):
            url = "https://" + url

        session = await self._get_session()
        chain = []
        current = url
        visited = set()

        for hop in range(max_hops):
            if current in visited:
                return {
                    "success": False,
                    "final_url": current,
                    "chain": chain,
                    "error": "redirect_loop",
                }
            visited.add(current)

            try:
                async with session.get(
                    current, allow_redirects=False, ssl=None
                ) as resp:
                    status = resp.status
                    headers = dict(resp.headers)
                    chain.append(
                        {
                            "url": current,
                            "status": status,
                            "location": headers.get("Location", ""),
                        }
                    )

                    if status in (301, 302, 303, 307, 308):
                        location = headers.get("Location", "")
                        if not location:
                            return {
                                "success": False,
                                "final_url": current,
                                "chain": chain,
                                "error": "missing_location",
                            }
                        current = _normalize_location(current, location)
                        continue

                    if status in (200, 201, 202, 204):
                        return {
                            "success": True,
                            "final_url": current,
                            "chain": chain,
                            "final_status": status,
                            "redirect_count": hop,
                            "headers": dict(resp.headers),
                        }

                    return {
                        "success": False,
                        "final_url": current,
                        "chain": chain,
                        "error": f"unexpected_status_{status}",
                    }

            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                return {
                    "success": False,
                    "final_url": current,
                    "chain": chain,
                    "error": f"{type(e).__name__}: {e}",
                }

        return {
            "success": False,
            "final_url": current,
            "chain": chain,
            "error": "max_hops_exceeded",
        }
