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
STALL_TIMEOUT = 10000


class BrowserHandler:
    def __init__(self):
        self._playwright = None
        self._browser = None

    async def _ensure_browser(self):
        if not HAS_PLAYWRIGHT:
            return
        if self._browser is None:
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
            await self._browser.close()
            self._browser = None
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None

    async def resolve(self, url: str) -> Optional[str]:
        if not HAS_PLAYWRIGHT:
            logger.debug("Playwright not installed, skipping browser handler")
            return None

        if not url.startswith(("http://", "https://")):
            url = "https://" + url

        try:
            await self._ensure_browser()
        except Exception as e:
            logger.debug(f"Failed to launch browser: {e}")
            return None

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

            final_url = None

            async def on_navigation(response):
                nonlocal final_url
                final_url = response.url

            page.on("response", on_navigation)

            try:
                await page.goto(url, wait_until="networkidle", timeout=NAV_TIMEOUT)
            except PlaywrightTimeout:
                pass
            except Exception as e:
                logger.debug(f"Browser navigation error: {e}")

            await asyncio.sleep(2)

            current_url = page.url
            if current_url and current_url != url:
                return current_url

            try:
                await page.wait_for_timeout(STALL_TIMEOUT)
            except Exception:
                pass

            current_url = page.url
            if current_url and current_url != url:
                return current_url

            return None

        except Exception as e:
            logger.debug(f"Browser handler failed for {url}: {e}")
            return None
        finally:
            if page:
                await page.close()
            if context:
                await context.close()
