import re
from urllib.parse import urlparse

URL_REGEX = re.compile(
    r"https?://(?:[-\w.]|(?:%[\da-fA-F]{2}))+"
    r"(?::\d+)?"
    r"(?:/(?:[-\w./%\+~#&=@]*))?"
)


def extract_urls(text: str) -> list[str]:
    return URL_REGEX.findall(text)


def normalize_url(url: str) -> str:
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    return url


def extract_domain(url: str) -> str | None:
    url = normalize_url(url)
    try:
        parsed = urlparse(url)
        return parsed.hostname.lower()
    except Exception:
        return None


def is_short_url(url: str, known_domains: set[str]) -> bool:
    domain = extract_domain(url)
    if not domain:
        return False
    if domain in known_domains:
        return True
    return False
