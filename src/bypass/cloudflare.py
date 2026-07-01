import asyncio
import logging
from typing import Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

CLOUDFLARE_CHALLENGE_SIGNATURES = [
    "cf-browser-verification",
    "challenge-form",
    "__cf_chl_f_tk",
    "/cdn-cgi/challenge-platform",
    "Checking your browser",
    "Just a moment...",
    "DDoS protection",
    "cf-challenge",
]

try:
    import cloudscraper
    HAS_CLOUDSCRAPER = True
except ImportError:
    HAS_CLOUDSCRAPER = False

try:
    import curl_cffi.requests as curl_requests
    HAS_CURL_CFFI = True
except ImportError:
    HAS_CURL_CFFI = False


class CloudflareResolver:
    def __init__(self):
        self._scraper = None
        self._curl_session = None

    def _ensure_scraper(self):
        if not HAS_CLOUDSCRAPER:
            return
        if self._scraper is None:
            self._scraper = cloudscraper.create_scraper(
                interpreter="native",
                delay=10,
            )

    async def _ensure_curl_session(self):
        if not HAS_CURL_CFFI:
            return
        if self._curl_session is None:
            self._curl_session = curl_requests.AsyncSession(
                impersonate="chrome",
                timeout=30,
            )

    async def close(self):
        self._scraper = None
        if self._curl_session:
            await self._curl_session.close()
            self._curl_session = None

    @staticmethod
    def is_cloudflare_challenge(html: str) -> bool:
        for sig in CLOUDFLARE_CHALLENGE_SIGNATURES:
            if sig in html:
                return True
        return False

    async def resolve(self, url: str) -> Optional[str]:
        if not HAS_CLOUDSCRAPER and not HAS_CURL_CFFI:
            return None

        if not url.startswith(("http://", "https://")):
            url = "https://" + url

        if HAS_CLOUDSCRAPER:
            result = await self._resolve_cloudscraper(url)
            if result:
                return result

        if HAS_CURL_CFFI:
            result = await self._resolve_curl_impersonate(url)
            if result:
                return result

        return None

    async def _resolve_cloudscraper(self, url: str) -> Optional[str]:
        self._ensure_scraper()
        try:
            resp = await asyncio.to_thread(
                self._scraper.get,
                url,
                allow_redirects=True,
                timeout=30,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/135.0.0.0 Safari/537.36"
                    ),
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                },
            )
            final_url = str(resp.url)
            if final_url != url:
                return final_url
            text = resp.text
            if text and text.startswith("http"):
                return text
            return None
        except Exception as e:
            logger.debug(f"Cloudscraper failed for {url}: {e}")
            return None

    async def _resolve_curl_impersonate(self, url: str) -> Optional[str]:
        await self._ensure_curl_session()
        try:
            resp = await self._curl_session.get(
                url,
                impersonate="chrome",
                follow_redirects=True,
            )
            final_url = str(resp.url)
            if final_url != url:
                return final_url
            text = resp.text.strip()
            if text and text.startswith("http"):
                return text
            return None
        except Exception as e:
            logger.debug(f"curl_cffi Cloudflare fallback failed for {url}: {e}")
            return None

    async def fetch_with_cloudscraper(self, url: str) -> Optional[str]:
        result = await self._resolve_cloudscraper(url)
        if result:
            return result
        result = await self._resolve_curl_impersonate(url)
        return result
