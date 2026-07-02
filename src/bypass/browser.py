import asyncio
import logging
from typing import Optional

logger = logging.getLogger(__name__)

try:
    from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False


NAV_TIMEOUT = 30000
STALL_TIMEOUT = 5000
CLICK_TIMEOUT = 20000
COUNTDOWN_WAIT_MAX = 15000

DOWNLOAD_SELECTORS = [
    "a:has-text('Download')",
    "a:has-text('Skip Ad')",
    "a:has-text('Get Link')",
    "a:has-text('Continue')",
    "a:has-text('Proceed')",
    "a:has-text('Generate Link')",
    "a:has-text('Verify')",
    "a:has-text('Unlock')",
    "button:has-text('Download')",
    "button:has-text('Skip Ad')",
    "button:has-text('Get Link')",
    "button:has-text('Continue')",
    "button:has-text('Proceed')",
    "button:has-text('Generate Link')",
    "button:has-text('Verify')",
    "button:has-text('Unlock')",
    "button:has-text('I am human')",
    "#download",
    "#down",
    "#continueBtn",
    ".download-btn",
    ".btn-download",
    "[id*='download']",
    "[class*='download']",
    "[id*='continue']",
    "[id*='verify']",
    "input[value*='Verify']",
    "input[value*='verify']",
    "[class*='verify']",
    "[id*='unlock']",
    "button:has-text('Institutional')",
    "button:has-text('Coverage')",
]

NEEDS_INTERACTION_DOMAINS = [
    "whatsgrouphub.com",
    "lovezindagihai.com",
    "news.zindagihai.com",
    "adshort",
    "link4earn",
    "loot-link",
    "lootdest",
    "cpmlink",
    "shortconnect",
    "cpmlinks",
]


class BrowserHandler:
    def __init__(self):
        self._playwright = None
        self._browser = None

    async def _ensure_browser(self, force_reset=False):
        if not HAS_PLAYWRIGHT:
            return
        if force_reset or self._browser is None:
            if self._browser is not None:
                try:
                    await self._browser.close()
                except Exception:
                    pass
                self._browser = None
            if self._playwright is None:
                self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                ],
            )

    async def close(self):
        if self._browser:
            try:
                await self._browser.close()
            except Exception:
                pass
            self._browser = None
        if self._playwright:
            try:
                await self._playwright.stop()
            except Exception:
                pass
            self._playwright = None

    async def _with_crash_recovery(self, url: str, method: str) -> Optional[str]:
        if not HAS_PLAYWRIGHT:
            logger.debug("Playwright not installed, skipping browser handler")
            return None
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        for attempt in range(2):
            try:
                await self._ensure_browser(force_reset=(attempt > 0))
                if method == "resolve":
                    return await self._do_resolve(url)
                else:
                    return await self._do_resolve_with_click(url)
            except Exception as e:
                logger.debug(f"Browser {method} attempt {attempt+1} failed: {e}")
                self._browser = None
        return None

    async def resolve(self, url: str) -> Optional[str]:
        return await self._with_crash_recovery(url, "resolve")

    async def resolve_with_click(self, url: str) -> Optional[str]:
        return await self._with_crash_recovery(url, "resolve_with_click")

    @staticmethod
    def _requires_interaction(url: str) -> bool:
        url_lower = url.lower()
        for domain in NEEDS_INTERACTION_DOMAINS:
            if domain in url_lower:
                return True
        return False

    @staticmethod
    async def _extract_telegram(page) -> Optional[str]:
        try:
            links = await page.eval_on_selector_all(
                "a[href*='t.me'], a[href*='telegram.me'], a[href*='telegram.org']",
                "els => els.map(el => el.href).filter(h => h.match(/t\\.me|telegram\\.me/))",
            )
            if links and len(links) > 0:
                return links[0]
            links2 = await page.evaluate("""
                () => {
                    const results = [];
                    document.querySelectorAll('a').forEach(a => {
                        const h = a.href || '';
                        if (h.includes('t.me/') || h.includes('telegram.me/')) results.push(h);
                    });
                    return results;
                }
            """)
            if links2 and len(links2) > 0:
                return links2[0]
        except Exception:
            pass
        return None

    async def _try_interact(self, page, original_url: str) -> Optional[str]:
        seen_urls = {page.url, original_url}
        for _round in range(4):
            try:
                await page.wait_for_timeout(STALL_TIMEOUT)
            except Exception:
                pass

            current_url = page.url
            if current_url not in seen_urls:
                return current_url

            tg = await self._extract_telegram(page)
            if tg:
                return tg

            clicked = await self._click_download(page)
            if clicked:
                try:
                    await page.wait_for_timeout(STALL_TIMEOUT)
                except Exception:
                    pass
                try:
                    await page.wait_for_load_state("networkidle", timeout=CLICK_TIMEOUT)
                except Exception:
                    pass
                new_url = page.url
                if new_url not in seen_urls:
                    return new_url
                tg = await self._extract_telegram(page)
                if tg:
                    return tg
        return None

    async def _do_resolve(self, url: str) -> Optional[str]:
        await self._ensure_browser()
        context = None
        page = None
        try:
            context = await self._browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/135.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1920, "height": 1080},
                locale="en-US",
            )
            page = await context.new_page()

            try:
                await page.goto(url, wait_until="networkidle", timeout=NAV_TIMEOUT)
            except PlaywrightTimeout:
                pass
            except Exception as e:
                logger.debug(f"Browser navigation error: {e}")

            await asyncio.sleep(2)

            current_url = page.url
            if current_url and current_url != url:
                if self._requires_interaction(current_url):
                    result = await self._try_interact(page, url)
                    if result:
                        return result
                return current_url

            try:
                await page.wait_for_timeout(STALL_TIMEOUT)
            except Exception:
                pass

            current_url = page.url
            if current_url and current_url != url:
                if self._requires_interaction(current_url):
                    result = await self._try_interact(page, url)
                    if result:
                        return result
                return current_url

            result = await self._try_interact(page, url)
            if result:
                return result

            return None

        except Exception:
            raise
        finally:
            if page:
                await page.close()
            if context:
                await context.close()

    async def _do_resolve_with_click(self, url: str) -> Optional[str]:
        await self._ensure_browser()
        context = None
        page = None
        try:
            context = await self._browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/135.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1920, "height": 1080},
                locale="en-US",
            )
            page = await context.new_page()

            try:
                await page.goto(url, wait_until="load", timeout=NAV_TIMEOUT)
            except PlaywrightTimeout:
                pass
            except Exception as e:
                logger.debug(f"Browser navigation error: {e}")
                return None

            await asyncio.sleep(3)

            current_url = page.url
            if current_url and current_url != url:
                if self._requires_interaction(current_url):
                    result = await self._try_interact(page, url)
                    if result:
                        return result
                return current_url

            result = await self._try_interact(page, url)
            if result:
                return result

            return None

        except Exception:
            raise
        finally:
            if page:
                await page.close()
            if context:
                await context.close()

    async def _click_download(self, page) -> bool:
        for selector in DOWNLOAD_SELECTORS:
            try:
                btn = await page.wait_for_selector(
                    selector,
                    timeout=CLICK_TIMEOUT,
                    state="visible",
                )
                if btn:
                    await btn.click()
                    logger.debug(f"Clicked button: {selector}")
                    return True
            except Exception:
                continue
        return False
