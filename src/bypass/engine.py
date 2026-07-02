import asyncio
import logging
from typing import Optional
import aiohttp
import re

from src.domain_db.db import DomainDB
from src.domain_db.checker import DomainChecker
from .redirect import RedirectResolver
from .api_fallback import BypassVIPAPI, GenericPaidAPI, RotatingBypassAPI
from .browser import BrowserHandler
from .smart_resolver import SmartResolver
from .cloudflare import CloudflareResolver
from .domain_specific import DomainSpecificHandler
from .nicktrick import NicktrickResolver
from .online_fallback import OnlineBypassFallback
from ..features import strip_tracking, safety_flags, fetch_og_preview
from config import config

logger = logging.getLogger(__name__)


class BypassResult:
    def __init__(
        self,
        success: bool,
        original_url: str,
        final_url: Optional[str] = None,
        method: str = "",
        error: str = "",
        domain_status: Optional[dict] = None,
        redirect_chain: Optional[list] = None,
        og_preview: Optional[dict] = None,
        safety: Optional[list[str]] = None,
    ):
        self.success = success
        self.original_url = original_url
        self.final_url = final_url or original_url
        self.method = method
        self.error = error
        self.domain_status = domain_status or {}
        self.redirect_chain = redirect_chain or []
        self.og_preview = og_preview
        self.safety = safety

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "original_url": self.original_url,
            "final_url": self.final_url,
            "method": self.method,
            "error": self.error,
            "domain": self.domain_status.get("domain", ""),
            "domain_status": self.domain_status.get("status", ""),
            "redirect_chain": self.redirect_chain,
            "og_preview": self.og_preview,
            "safety": self.safety,
        }

    def user_message(self) -> str:
        domain = self.domain_status.get("domain", "")
        status = self.domain_status.get("status", "")

        if not self.success:
            msg = f"❌ <b>Could not bypass</b>\n"
            msg += f"URL: <code>{self.original_url}</code>\n"

            if status == "inactive":
                notes = self.domain_status.get("notes", "")
                error = self.domain_status.get("error", "")
                info = notes or error
                msg += f"\n⚠️ <b>Shortener is no longer active</b>"
                if info:
                    msg += f"\nReason: {info}"
            elif self.error:
                msg += f"\nError: {self.error}"
            else:
                msg += f"\nNo bypass handler succeeded."
            return msg

        clean_url = strip_tracking(self.final_url)
        flags = self.safety or safety_flags(self.final_url)

        msg = f"✅ <b>Bypassed</b>"
        if domain:
            msg += f" ({domain})"

        if self.og_preview:
            og = self.og_preview
            if og.get("title"):
                msg += f"\n\n📄 <b>{og['title'][:200]}</b>"
            if og.get("description"):
                desc = og["description"][:300]
                msg += f"\n{desc}"

        msg += f"\n\n📎 <b>Original:</b>\n<code>{self.original_url}</code>"
        msg += f"\n\n🔗 <b>Destination:</b>\n<code>{clean_url}</code>"

        extras = []
        if self.method:
            extras.append(f"Method: {self.method}")
        if self.redirect_chain:
            chain_str = " → ".join(
                f"{h['status']}" for h in self.redirect_chain[:5]
            )
            if len(self.redirect_chain) > 5:
                chain_str += f" → ... (+{len(self.redirect_chain)-5})"
            extras.append(f"Redirects: {chain_str}")
        if clean_url != self.final_url:
            extras.append("Tracking params stripped")

        if flags:
            flag_labels = {
                "non_https": "⚠️ Non-HTTPS destination",
                "suspicious_tld": "⚠️ Suspicious TLD",
                "very_long_url": "⚠️ Very long URL",
                "many_params": "⚠️ Many query parameters",
                "url_has_at_symbol": "⚠️ URL contains @ symbol",
            }
            for f in flags:
                label = flag_labels.get(f, f)
                extras.append(label)

        if extras:
            msg += "\n\n" + "\n".join(f"{e}" for e in extras)

        return msg


class BypassEngine:
    MAX_RECURSION_DEPTH = 5
    BYPASS_TIMEOUT = 60

    def __init__(self, db: DomainDB, checker: DomainChecker):
        self.db = db
        self.checker = checker
        self.redirect_resolver = RedirectResolver()
        self.smart_resolver = SmartResolver()
        self.cloudflare = CloudflareResolver()
        self.domain_specific = DomainSpecificHandler()
        self.nicktrick = NicktrickResolver()
        self.rotating_api = RotatingBypassAPI()
        self.bypass_vip = BypassVIPAPI(api_key=config.bypass_vip_api_key)
        self.browser = BrowserHandler()
        self.generic_api = GenericPaidAPI(
            api_key=config.bypass_tools_api_key,
            base_url="https://api.bypass.tools/api/v1/bypass",
        )
        self.online_fallback = OnlineBypassFallback()

    @staticmethod
    def _is_valid_result(original_url: str, final_url: str) -> bool:
        if not final_url or final_url == original_url:
            return False
        if BypassEngine._is_tracking_url(final_url):
            return False
        final_lower = final_url.lower()
        bad_patterns = [
            "enable javascript",
            "enable js",
            "your browser does not support javascript",
            "sorry, you have been blocked",
            "access denied",
            "500 internal server error",
            "502 bad gateway",
            "503 service unavailable",
            "404 not found",
        ]
        for pat in bad_patterns:
            if pat in final_lower:
                return False
        return True

    @staticmethod
    def _is_tracking_url(url: str) -> bool:
        url_lower = url.lower()
        tracking_patterns = [
            "adlinkfly=",
            "mtc1.",
            "nclsil.in",
            "flexthecar.com",
            "adclick",
            "adserv.",
            "trk.",
            "advertising/",
        ]
        for pat in tracking_patterns:
            if pat in url_lower:
                return True
        return False

    @staticmethod
    def _looks_like_gate_url(url: str) -> bool:
        """Check if a URL looks like an ad-wall gate page, not a real bypass result."""
        gate_domains = [
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
            "ad2boost",
            "adshrink",
            "admonkey",
            "adbimat",
            "adclick",
            "exe.io",
            "fc.lc",
            "shrinkme",
            "za.gl",
            "bc.vc",
            "nclsil.in",
            "flexthecar.com",
        ]
        url_lower = url.lower()
        for domain in gate_domains:
            if domain in url_lower:
                return True
        gate_paths = [
            "/educationinsurancess/",
            "/scholarship",
            "/insurance",
        ]
        for path in gate_paths:
            if path in url_lower:
                return True
        return False

    async def close(self):
        await self.redirect_resolver.close()
        await self.smart_resolver.close()
        await self.cloudflare.close()
        await self.rotating_api.close()
        self.domain_specific = None
        await self.nicktrick.close()
        await self.bypass_vip.close()
        await self.browser.close()
        await self.generic_api.close()
        await self.online_fallback.close()

    async def _finalize(self, result: BypassResult, _depth: int) -> BypassResult:
        if _depth == 0 and result.success:
            return await self._enrich(result)
        return result

    async def _try_redirect(self, url: str) -> Optional[dict]:
        result = await self.redirect_resolver.resolve(url)
        if result and result.get("success") and result.get("final_url") == url:
            return None
        return result

    async def _try_smart_resolve(self, url: str) -> Optional[str]:
        return await self.smart_resolver.resolve(url)

    async def _try_cloudflare(self, url: str) -> Optional[str]:
        return await self.cloudflare.resolve(url)

    async def _try_nicktrick(self, url: str) -> Optional[str]:
        return await self.nicktrick.resolve(url)

    async def _try_domain_specific(self, url: str) -> Optional[str]:
        return await self.domain_specific.resolve(url)

    async def _try_bypass_vip(self, url: str) -> Optional[str]:
        return await self.bypass_vip.bypass(url)

    async def _try_rotating_api(self, url: str) -> Optional[str]:
        return await self.rotating_api.bypass(url)

    async def _try_browser(self, url: str) -> Optional[str]:
        return await self.browser.resolve(url)

    async def _try_generic_api(self, url: str) -> Optional[str]:
        return await self.generic_api.bypass(url)

    async def _try_online_fallback(self, url: str) -> Optional[str]:
        return await self.online_fallback.bypass(url)

    async def _enrich(self, result: BypassResult) -> BypassResult:
        if result.success and result.final_url:
            try:
                connector = aiohttp.TCPConnector(limit=1)
                async with aiohttp.ClientSession(connector=connector) as session:
                    og = await fetch_og_preview(result.final_url, session)
                    if og:
                        result.og_preview = og
            except Exception:
                pass
        return result

    async def bypass(self, url: str, _original_url: str = None, _depth: int = 0) -> BypassResult:
        if _original_url is None:
            _original_url = url

        domain_result = await self.checker.check_url(url)
        domain_status = domain_result.get("status", "unknown")

        if domain_status == "inactive":
            return BypassResult(
                success=False,
                original_url=_original_url,
                error=f"Domain {domain_result.get('domain', '')} inactive",
                domain_status=domain_result,
            )

        cached = self.db.get_cached_bypass(url)
        if cached:
            if self._is_valid_result(url, cached):
                if _depth < self.MAX_RECURSION_DEPTH:
                    next_check = await self.checker.check_url(cached)
                    if next_check.get("status") == "active" and not self._looks_like_gate_url(cached):
                        deeper = await self.bypass(cached, _original_url, _depth + 1)
                        if deeper.success:
                            return await self._finalize(deeper, _depth)
                return await self._finalize(BypassResult(
                    success=True,
                    original_url=_original_url,
                    final_url=cached,
                    method="cache",
                    domain_status=domain_result,
                ), _depth)
            logger.debug(f"Invalid cached result for {url}: {cached}")

        async def _run_handlers():
            has_domain_route = self.domain_specific.has_route(url)
            handlers = []
            if not has_domain_route:
                handlers.extend([
                    ("http_redirect", self._try_redirect),
                    ("smart_resolver", self._try_smart_resolve),
                    ("cloudflare", self._try_cloudflare),
                    ("nicktrick", self._try_nicktrick),
                ])
            handlers.extend([
                ("domain_specific", self._try_domain_specific),
                ("bypass_vip", self._try_bypass_vip),
                ("browser", self._try_browser),
                ("rotating_api", self._try_rotating_api),
                ("generic_api", self._try_generic_api),
                ("online_fallback", self._try_online_fallback),
            ])

            for method_name, handler in handlers:
                try:
                    result = await handler(url)
                    if not result:
                        continue
                    if isinstance(result, dict) and result.get("success"):
                        final = result["final_url"]
                        redirect_chain = result.get("chain", [])
                        if self._is_tracking_url(final):
                            logger.debug(f"Handler {method_name} returned tracking URL {final}, skipping")
                            continue
                    elif isinstance(result, str):
                        if method_name != "http_redirect" and self._looks_like_gate_url(result):
                            logger.debug(f"Handler {method_name} returned gate URL {result}, skipping")
                            continue
                        if self._is_tracking_url(result):
                            logger.debug(f"Handler {method_name} returned tracking URL {result}, skipping")
                            continue
                        final = result
                        redirect_chain = []
                    else:
                        continue

                    if self._is_valid_result(url, final):
                        self.db.set_bypass_cache(url, final)
                    else:
                        logger.debug(f"Not caching invalid result for {url}: {final}")
                        continue

                    if _depth < self.MAX_RECURSION_DEPTH:
                        next_check = await self.checker.check_url(final)
                        if (
                            next_check.get("status") == "active"
                            and not self._looks_like_gate_url(final)
                        ):
                            deeper = await self.bypass(final, _original_url, _depth + 1)
                            if deeper.success:
                                return await self._finalize(deeper, _depth)

                    return await self._finalize(BypassResult(
                        success=True,
                        original_url=_original_url,
                        final_url=final,
                        method=method_name,
                        domain_status=domain_result,
                        redirect_chain=redirect_chain,
                    ), _depth)

                except Exception as e:
                    logger.debug(f"Handler {method_name} failed for {url}: {e}")
                    continue

            return BypassResult(
                success=False,
                original_url=_original_url,
                error="All bypass handlers failed",
                domain_status=domain_result,
            )

        if _depth == 0:
            try:
                return await asyncio.wait_for(_run_handlers(), timeout=self.BYPASS_TIMEOUT)
            except asyncio.TimeoutError:
                logger.warning(f"Bypass timed out after {self.BYPASS_TIMEOUT}s for {url}")
                return BypassResult(
                    success=False,
                    original_url=_original_url,
                    error=f"Bypass timed out after {self.BYPASS_TIMEOUT}s",
                    domain_status=domain_result,
                )
        return await _run_handlers()
