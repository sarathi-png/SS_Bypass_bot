import logging
import re
from typing import Optional
from urllib.parse import urlparse, urlencode, parse_qs, urlunparse

logger = logging.getLogger(__name__)

TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "fbclid", "gclid", "gclsrc", "dclid", "msclkid",
    "mc_eid", "yclid", "igshid", "ref", "ref_src", "ref_url",
    "source", "si", "s", "spm", "scm",
}


def strip_tracking(url: str) -> str:
    parsed = urlparse(url)
    if not parsed.query:
        return url
    params = parse_qs(parsed.query, keep_blank_values=True)
    cleaned = {k: v for k, v in params.items() if k.lower() not in TRACKING_PARAMS}
    if not cleaned:
        new_query = ""
    else:
        new_query = urlencode(cleaned, doseq=True)
    return urlunparse(parsed._replace(query=new_query))


async def fetch_og_preview(url: str, session) -> Optional[dict]:
    try:
        async with session.get(
            url,
            timeout=aiohttp.ClientTimeout(total=10),
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/135.0.0.0 Safari/537.36"
                )
            },
        ) as resp:
            if resp.status != 200:
                return None
            html = await resp.text()
    except Exception:
        return None

    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        return None

    og = {}
    for meta in soup.find_all("meta"):
        prop = (meta.get("property") or "").lower()
        name = (meta.get("name") or "").lower()
        content = meta.get("content", "")

        if prop == "og:title" or name == "twitter:title":
            og["title"] = content
        elif prop == "og:description" or name == "twitter:description":
            og["description"] = content
        elif prop == "og:image" or name == "twitter:image":
            og["image"] = content
        elif prop == "og:site_name":
            og["site_name"] = content

    if not og.get("title"):
        title_tag = soup.find("title")
        if title_tag:
            og["title"] = title_tag.get_text(strip=True)

    return og or None


import aiohttp

TRACKING_DOMAINS = {
    "doubleclick.net", "googleadservices.com", "googlesyndication.com",
    "facebook.com/tr", "analytics.twitter.com", "ads.linkedin.com",
}


def safety_flags(url: str) -> list[str]:
    flags = []
    parsed = urlparse(url)

    if parsed.scheme != "https":
        flags.append("non_https")

    domain = parsed.hostname or ""
    if any(td in domain for td in TRACKING_DOMAINS):
        flags.append("tracking_domain")

    suspicious_tlds = {".tk", ".ml", ".ga", ".cf", ".gq"}
    for tld in suspicious_tlds:
        if domain.endswith(tld):
            flags.append("suspicious_tld")
            break

    if len(url) > 500:
        flags.append("very_long_url")

    params = parse_qs(parsed.query)
    total_params = sum(len(v) for v in params.values())
    if total_params > 20:
        flags.append("many_params")

    if "@" in parsed.path or "@" in parsed.query:
        flags.append("url_has_at_symbol")

    return flags
