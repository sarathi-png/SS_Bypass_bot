import asyncio
import logging
from typing import Optional

import aiohttp

logger = logging.getLogger(__name__)

BYPASS_VIP_URL = "https://api.bypass.vip/"


class BypassVIPAPI:
    def __init__(self, api_key: str = ""):
        self.api_key = api_key
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=30),
                headers={"User-Agent": "telegram-bypass-bot/1.0"},
            )
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    async def bypass(self, url: str) -> Optional[str]:
        session = await self._get_session()
        params = {"url": url}
        if self.api_key:
            params["key"] = self.api_key

        try:
            async with session.get(BYPASS_VIP_URL, params=params) as resp:
                if resp.status != 200:
                    logger.debug(f"bypass.vip returned {resp.status}")
                    return None
                data = await resp.json()
                if data.get("status") == "success":
                    return data.get("result")
                logger.debug(f"bypass.vip error: {data.get('message', 'unknown')}")
                return None
        except (aiohttp.ClientError, asyncio.TimeoutError, Exception) as e:
            logger.debug(f"bypass.vip request failed: {e}")
            return None


class GenericPaidAPI:
    def __init__(self, api_key: str = "", base_url: str = ""):
        self.api_key = api_key
        self.base_url = base_url
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=30),
                headers={
                    "User-Agent": "telegram-bypass-bot/1.0",
                    "Content-Type": "application/json",
                },
            )
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    async def bypass(self, url: str) -> Optional[str]:
        if not self.api_key or not self.base_url:
            return None
        session = await self._get_session()
        try:
            async with session.post(
                self.base_url,
                json={"url": url},
                headers={"Authorization": f"Bearer {self.api_key}"},
            ) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
                for key in ("result_url", "destination_url", "result", "finalUrl", "destination"):
                    if key in data:
                        return data[key]
                return None
        except Exception as e:
            logger.debug(f"Generic paid API failed: {e}")
            return None
