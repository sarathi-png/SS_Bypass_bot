import asyncio
import logging
import re
from typing import Optional
from urllib.parse import urljoin, urlparse

logger = logging.getLogger(__name__)

try:
    import curl_cffi.requests as curl_requests

    HAS_CURL_CFFI = True
except ImportError:
    HAS_CURL_CFFI = False
    curl_requests = None

try:
    from bs4 import BeautifulSoup

    HAS_BS4 = True
except ImportError:
    HAS_BS4 = False

from .html_parser import HTMLRedirectParser

TIMEOUT = 25
MAX_ATTEMPTS = 3
MAX_WAIT = 10

COMMON_AJAX_ENDPOINTS = [
    "/ajax.php?ajax=download",
    "/ajax.php?ajax=get_link",
    "/ajax.php?ajax=go",
    "/ajax.php?ajax=redirect",
    "/ajax.php?ajax=info",
    "/ajax/get_link",
    "/ajax/go",
    "/ajax/redirect",
    "/ajax/skip",
    "/api/v1/bypass",
    "/api/get_url",
    "/api/redirect",
    "/api/bypass",
    "/bypass",
    "/go",
    "/redirect",
    "/out",
    "/link/get",
    "/ad/get",
    "/ad/redirect",
    "/download",
]

COUNTER_RE = re.compile(
    r'(?:counter_value|countdown|seconds|timer|counter|wait)\s*[:=]\s*(\d+)',
    re.IGNORECASE,
)
DOWNLOAD_BTN_RE = re.compile(
    r'(?:skip|continue|get link|proceed|go to link|download|generate)',
    re.IGNORECASE,
)
GDRIVE_FILEID_RE = re.compile(
    r'(?:fileid|file_id|gdrive_id|drive_id|gd_id)[=:\s]+([a-zA-Z0-9_\-]{20,})',
    re.IGNORECASE,
)


class FormData:
    def __init__(self, url: str, fields: dict, submit_text: str = ""):
        self.url = url
        self.fields = fields
        self.submit_text = submit_text


class SmartResolver:
    def __init__(self):
        self._session = None
        self.html_parser = HTMLRedirectParser()

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

    async def _fetch(self, url: str, follow_redirects: bool = False) -> Optional[dict]:
        if not HAS_CURL_CFFI:
            return None
        await self._ensure_session()
        try:
            resp = await self._session.get(
                url,
                impersonate="chrome",
                follow_redirects=follow_redirects,
            )
            return {
                "success": True,
                "status": resp.status_code,
                "headers": dict(resp.headers),
                "text": resp.text,
                "url": str(resp.url),
            }
        except Exception as e:
            logger.debug(f"SmartResolver fetch failed for {url}: {e}")
            return None

    async def _resolve_redirect(self, url: str, location: str) -> Optional[str]:
        if not location.startswith(("http://", "https://")):
            parsed = urlparse(url)
            if location.startswith("//"):
                location = f"{parsed.scheme}:{location}"
            elif location.startswith("/"):
                location = f"{parsed.scheme}://{parsed.netloc}{location}"
            else:
                base = url.rsplit("/", 1)[0]
                location = f"{base}/{location}"
        result = await self._fetch(location, follow_redirects=True)
        if result and result.get("success"):
            return result["url"]
        return location

    async def _try_form_post(self, html: str, base_url: str) -> Optional[str]:
        if not HAS_BS4:
            return None
        try:
            soup = BeautifulSoup(html, "lxml")
        except Exception:
            return None

        for form_tag in soup.find_all("form"):
            action = form_tag.get("action", "")
            method = form_tag.get("method", "get").lower()
            if method != "post":
                continue

            inputs = form_tag.find_all("input", type="hidden")
            fields = {}
            for inp in inputs:
                name = inp.get("name")
                value = inp.get("value", "")
                if name:
                    fields[name] = value

            has_csrf = any(
                "csrf" in k.lower() or k.startswith("_") for k in fields
            )
            has_ad_type = any("ad" in k.lower() for k in fields)
            has_accept = any("accept" in k.lower() for k in fields)

            if not (has_csrf or has_ad_type or has_accept):
                continue

            action_url = urljoin(base_url, action) if action else base_url
            logger.debug(f"Form POST to {action_url} with {len(fields)} fields")
            result = await self._post_form(action_url, fields)
            if result:
                return result

        return None

    async def _post_form(self, url: str, fields: dict) -> Optional[str]:
        if not HAS_CURL_CFFI:
            return None
        await self._ensure_session()
        try:
            resp = await self._session.post(
                url,
                data=fields,
                impersonate="chrome",
                follow_redirects=True,
            )
            final_url = str(resp.url)
            if final_url != url:
                return final_url
            text = resp.text.strip()
            if text:
                if text.startswith("http"):
                    return text
                try:
                    data = resp.json()
                    if isinstance(data, dict):
                        for key in ("url", "link", "redirect", "destination", "goto", "location", "file"):
                            val = data.get(key)
                            if val and isinstance(val, str) and val.startswith("http"):
                                return val
                except Exception:
                    pass
            return None
        except Exception as e:
            logger.debug(f"Form POST failed for {url}: {e}")
            return None

    def _detect_countdown(self, html: str) -> Optional[int]:
        match = COUNTER_RE.search(html)
        if match:
            return min(int(match.group(1)), MAX_WAIT)
        return None

    async def _probe_endpoints(self, base_url: str) -> Optional[str]:
        if not HAS_CURL_CFFI:
            return None
        await self._ensure_session()

        parsed = urlparse(base_url)
        base = f"{parsed.scheme}://{parsed.netloc}"

        ajax_headers = {
            "X-Requested-With": "XMLHttpRequest",
            "Referer": base_url,
        }

        for endpoint in COMMON_AJAX_ENDPOINTS:
            url = f"{base}{endpoint}"
            try:
                resp = await self._session.get(
                    url,
                    headers=ajax_headers,
                    impersonate="chrome",
                    follow_redirects=True,
                )
                if resp.status_code != 200:
                    continue
                text = resp.text.strip()
                if not text:
                    continue
                if text.startswith("http"):
                    return text
                try:
                    data = resp.json()
                    if isinstance(data, dict):
                        if data.get("code") == "200" and data.get("file"):
                            return data["file"]
                        for key in ("url", "link", "redirect", "destination", "goto", "location", "file"):
                            val = data.get(key)
                            if val and isinstance(val, str) and val.startswith("http"):
                                return val
                except Exception:
                    pass
            except Exception:
                continue

        return None

    def _extract_hidden_values(self, html: str, base_url: str) -> Optional[str]:
        match = GDRIVE_FILEID_RE.search(html)
        if match:
            file_id = match.group(1)
            return f"https://drive.google.com/uc?id={file_id}"
        return None

    async def resolve(self, url: str) -> Optional[str]:
        if not HAS_CURL_CFFI:
            return None

        url = url if url.startswith(("http://", "https://")) else f"https://{url}"

        for attempt in range(MAX_ATTEMPTS):
            result = await self._fetch(url, follow_redirects=False)
            if not result:
                return None

            current_url = result["url"]
            html = result["text"]
            status = result["status"]
            headers = result["headers"]

            if status in (301, 302, 303, 307, 308):
                location = headers.get("location", "")
                if location:
                    return await self._resolve_redirect(current_url, location)

            if current_url != url:
                return current_url

            js_url = self.html_parser.extract_html_redirect(html, current_url)
            if js_url:
                resolved = await self._fetch(js_url, follow_redirects=True)
                if resolved and resolved.get("success"):
                    return resolved["url"]
                return js_url

            form_url = await self._try_form_post(html, current_url)
            if form_url:
                return form_url

            wait = self._detect_countdown(html)
            if wait:
                logger.debug(f"Countdown {wait}s detected, waiting")
                await asyncio.sleep(wait)
                continue

            api_url = await self._probe_endpoints(current_url)
            if api_url:
                return api_url

            gdrive_url = self._extract_hidden_values(html, current_url)
            if gdrive_url:
                return gdrive_url

            break

        return None
