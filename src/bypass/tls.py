import asyncio
import logging
from typing import Optional

logger = logging.getLogger(__name__)

try:
    import curl_cffi.requests as curl_requests

    HAS_CURL_CFFI = True
except ImportError:
    HAS_CURL_CFFI = False
    curl_requests = None

TIMEOUT = 20


class TLSImpersonator:
    def __init__(self):
        self._session = None

    async def _ensure_session(self):
        if not HAS_CURL_CFFI:
            return
        if self._session is None:
            self._session = curl_requests.AsyncSession(
                impersonate="chrome",
                timeout=TIMEOUT,
            )

    async def close(self):
        if self._session:
            await self._session.close()
            self._session = None

    async def fetch(self, url: str, follow_redirects: bool = False) -> Optional[dict]:
        if not HAS_CURL_CFFI:
            logger.debug("curl_cffi not installed, skipping TLS impersonation")
            return None

        if not url.startswith(("http://", "https://")):
            url = "https://" + url

        await self._ensure_session()

        try:
            resp = await self._session.get(
                url,
                impersonate="chrome",
                follow_redirects=follow_redirects,
            )
            result = {
                "success": True,
                "status": resp.status_code,
                "headers": dict(resp.headers),
                "text": resp.text,
                "url": str(resp.url),
            }
            return result
        except Exception as e:
            logger.debug(f"TLS impersonation failed for {url}: {e}")
            return None

    async def get_redirect_url(self, url: str) -> Optional[str]:
        if not HAS_CURL_CFFI:
            return None

        await self._ensure_session()
        if not url.startswith(("http://", "https://")):
            url = "https://" + url

        try:
            resp = await self._session.get(
                url,
                impersonate="chrome",
                follow_redirects=True,
                max_redirects=5,
            )
            final_url = str(resp.url)
            if final_url != url:
                return final_url
            location = resp.headers.get("location", "")
            if location:
                from urllib.parse import urlparse

                if not location.startswith("http"):
                    parsed = urlparse(url)
                    if location.startswith("//"):
                        location = f"{parsed.scheme}:{location}"
                    elif location.startswith("/"):
                        location = f"{parsed.scheme}://{parsed.netloc}{location}"
                    else:
                        base = url.rsplit("/", 1)[0]
                        location = f"{base}/{location}"
                return location
            return None
        except Exception as e:
            logger.debug(f"TLS redirect failed for {url}: {e}")
            return None
