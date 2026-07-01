import logging
from typing import Optional

from src.domain_db.db import DomainDB
from src.domain_db.checker import DomainChecker
from .redirect import RedirectResolver
from .tls import TLSImpersonator
from .html_parser import HTMLRedirectParser
from .api_fallback import BypassVIPAPI, GenericPaidAPI
from .browser import BrowserHandler
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
    def __init__(self, db: DomainDB, checker: DomainChecker):
        self.db = db
        self.checker = checker
        self.redirect_resolver = RedirectResolver()
        self.tls = TLSImpersonator()
        self.bypass_vip = BypassVIPAPI(api_key=config.bypass_vip_api_key)
        self.browser = BrowserHandler()
        self.generic_api = GenericPaidAPI(
            api_key=config.bypass_tools_api_key,
            base_url="https://api.bypass.tools/api/v1/bypass",
        )

    async def close(self):
        await self.redirect_resolver.close()
        await self.tls.close()
        await self.bypass_vip.close()
        await self.browser.close()
        await self.generic_api.close()

    async def _try_redirect(self, url: str) -> Optional[dict]:
        result = await self.redirect_resolver.resolve(url)
        if result and result.get("success") and result.get("final_url") == url:
            return None
        return result

    async def _try_tls_fetch(self, url: str) -> Optional[str]:
        result = await self.tls.fetch(url, follow_redirects=True)
        if not result or not result.get("success"):
            return None
        final_url = result.get("url", "")
        if final_url and final_url != url:
            return final_url
        return None

    async def _try_html_parse(self, url: str) -> Optional[str]:
        result = await self.tls.fetch(url, follow_redirects=False)
        if not result or not result.get("success"):
            return None
        html = result.get("text", "")
        final_url = result.get("url", url)
        redirects = HTMLRedirectParser.extract_all(html, final_url)
        for r in redirects:
            target = r["url"]
            if target and target != url:
                resolved = await self.redirect_resolver.resolve(target)
                if resolved["success"]:
                    return resolved["final_url"]
                return target
        return None

    async def _try_bypass_vip(self, url: str) -> Optional[str]:
        return await self.bypass_vip.bypass(url)

    async def _try_browser(self, url: str) -> Optional[str]:
        return await self.browser.resolve(url)

    async def _try_generic_api(self, url: str) -> Optional[str]:
        return await self.generic_api.bypass(url)

    async def _enrich(self, result: BypassResult) -> BypassResult:
        if result.success and result.final_url:
            import aiohttp
            try:
                connector = aiohttp.TCPConnector(limit=1)
                async with aiohttp.ClientSession(connector=connector) as session:
                    og = await fetch_og_preview(result.final_url, session)
                    if og:
                        result.og_preview = og
            except Exception:
                pass
        return result

    async def bypass(self, url: str) -> BypassResult:
        domain_result = await self.checker.check_url(url)
        domain_status = domain_result.get("status", "unknown")

        if domain_status == "inactive":
            return BypassResult(
                success=False,
                original_url=url,
                error="Domain inactive",
                domain_status=domain_result,
            )

        cached = self.db.get_cached_bypass(url)
        if cached:
            return await self._enrich(BypassResult(
                success=True,
                original_url=url,
                final_url=cached,
                method="cache",
                domain_status=domain_result,
            ))

        handlers = [
            ("http_redirect", self._try_redirect),
            ("tls_impersonation", self._try_tls_fetch),
            ("html_parse", self._try_html_parse),
            ("bypass_vip", self._try_bypass_vip),
            ("browser", self._try_browser),
            ("generic_api", self._try_generic_api),
        ]

        for method_name, handler in handlers:
            try:
                result = await handler(url)
                if not result:
                    continue
                if isinstance(result, dict) and result.get("success"):
                    final = result["final_url"]
                    self.db.set_bypass_cache(url, final)
                    return await self._enrich(BypassResult(
                        success=True,
                        original_url=url,
                        final_url=final,
                        method=method_name,
                        domain_status=domain_result,
                        redirect_chain=result.get("chain", []),
                    ))
                if isinstance(result, str):
                    self.db.set_bypass_cache(url, result)
                    return await self._enrich(BypassResult(
                        success=True,
                        original_url=url,
                        final_url=result,
                        method=method_name,
                        domain_status=domain_result,
                    ))
            except Exception as e:
                logger.debug(f"Handler {method_name} failed for {url}: {e}")
                continue

        return BypassResult(
            success=False,
            original_url=url,
            error="All bypass handlers failed",
            domain_status=domain_result,
        )
