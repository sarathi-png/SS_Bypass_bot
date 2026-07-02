import asyncio
import logging
from typing import Optional
from urllib.parse import urlparse
import re

logger = logging.getLogger(__name__)

FOLLOW_CHAIN_TIMEOUT = 30

# Patterns matching ad-wall verification gate pages
VERIFICATION_GATE_PATTERNS = [
    r"Verify\s+Institutional",
    r"to\s+unlock\s+the\s+link",
    r"id=['\"]continueBtn['\"]",
    r"button[^>]*>Verify\s+",
    r"Please\s+Verify",
    r"Click\s+to\s+unlock",
    r"Verify\s+You\s+Are\s+Human",
    r"I'?m?\s+not\s+a\s+robot",
    r"g-recaptcha",
    r"h-captcha",
    r"data-sitekey",
]

# Signatures unique to actual Cloudflare challenge/interstitial pages
# (not scripts/assets that appear on normal CF-proxied pages)
CLOUDFLARE_CHALLENGE_SIGNATURES = [
    "cf-browser-verification",
    "challenge-form",
    "__cf_chl_f_tk",
    "Checking your browser",
    "Just a moment...",
    "cf-error-details",
    "cf-error-overview",
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
        if not html:
            return False
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
            if not text:
                return None
            if text.startswith("http"):
                return text
            if self.is_cloudflare_challenge(text):
                return None
            chain_result = await asyncio.wait_for(
                self._follow_chain(url), timeout=FOLLOW_CHAIN_TIMEOUT
            )
            if chain_result is None:
                return None
            if chain_result != url:
                return chain_result
            redirect_url = self._extract_and_follow(url, text)
            if redirect_url:
                return redirect_url
            return final_url
        except asyncio.TimeoutError:
            logger.debug(f"Chain follow timed out for {url}")
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
                allow_redirects=True,
            )
            final_url = str(resp.url)
            if final_url != url:
                return final_url
            text = resp.text.strip()
            if not text:
                return None
            if text.startswith("http"):
                return text
            if self.is_cloudflare_challenge(text):
                return None

            # Check for verification gate in initial response
            for pat in VERIFICATION_GATE_PATTERNS:
                if re.search(pat, text, re.IGNORECASE):
                    return None

            chain_result = await asyncio.wait_for(
                self._follow_chain(url), timeout=FOLLOW_CHAIN_TIMEOUT
            )
            if chain_result is None:
                return None
            if chain_result != url:
                return chain_result
            redirect_url = self._extract_and_follow(url, text)
            if redirect_url:
                return redirect_url
            return final_url
        except Exception as e:
            logger.debug(f"curl_cffi Cloudflare fallback failed for {url}: {e}")
            return None

    def _extract_and_follow(self, base_url: str, html: str) -> Optional[str]:
        """Extract redirect URLs from HTML after CF bypass and follow them."""
        from .html_parser import HTMLRedirectParser
        parser = HTMLRedirectParser()
        js_url = parser.extract_html_redirect(html, base_url)
        if js_url:
            return js_url
        return None

    async def _follow_chain(self, url: str, depth: int = 0, max_depth: int = 6) -> Optional[str]:
        """Recursively follow JS redirect chain after CF bypass."""
        if depth >= max_depth:
            return url

        if not HAS_CURL_CFFI:
            return url

        await self._ensure_curl_session()
        try:
            resp = await self._curl_session.get(
                url,
                impersonate="chrome",
                allow_redirects=True,
            )
            final_url = str(resp.url)
            if final_url != url:
                return await self._follow_chain(final_url, depth + 1, max_depth)

            text = resp.text.strip()
            if not text:
                return url
            if self.is_cloudflare_challenge(text):
                return url

            from .html_parser import HTMLRedirectParser
            parser = HTMLRedirectParser()
            js_url = parser.extract_html_redirect(text, final_url)
            if js_url:
                return await self._follow_chain(js_url, depth + 1, max_depth)

            # Check for verification gate
            for pat in VERIFICATION_GATE_PATTERNS:
                if re.search(pat, text, re.IGNORECASE):
                    return None

            return final_url
        except Exception as e:
            logger.debug(f"Chain follow failed at depth {depth} for {url}: {e}")
            return url

    async def fetch_with_cloudscraper(self, url: str) -> Optional[str]:
        result = await self._resolve_cloudscraper(url)
        if result:
            return result
        result = await self._resolve_curl_impersonate(url)
        return result
