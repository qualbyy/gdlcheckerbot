"""
GDL API Client — безопасный, rate-limited клиент к реальному API Demonlist (demonlist.org).
"""

import asyncio
import logging
import os
import time
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)

GDL_API = os.getenv("GDL_API_BASE", "https://api.demonlist.org")
MAX_PAGES = 10           # Жёсткий потолок пагинации — защита от бесконечных циклов
PAGE_SIZE_RECORDS = 50
PAGE_SIZE_LEVELS = 15
REQUEST_TIMEOUT = 12.0


class APIError(Exception):
    pass


class _TokenBucket:
    def __init__(self, rate: float = 4.0, burst: int = 8):
        self._rate = rate
        self._burst = burst
        self._tokens = float(burst)
        self._last = time.monotonic()
        self._lock: asyncio.Lock | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    def _get_lock(self) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        if self._lock is None or self._loop != loop:
            self._loop = loop
            self._lock = asyncio.Lock()
        return self._lock

    async def acquire(self) -> None:
        async with self._get_lock():
            now = time.monotonic()
            self._tokens = min(self._burst, self._tokens + (now - self._last) * self._rate)
            self._last = now
            if self._tokens < 1:
                await asyncio.sleep((1 - self._tokens) / self._rate)
                self._tokens = 0.0
            else:
                self._tokens -= 1.0


class GDLClient:
    def __init__(self, base_url: str | None = None) -> None:
        self._base_url = base_url or GDL_API
        self._session: aiohttp.ClientSession | None = None
        self._limiter = _TokenBucket(rate=4.0, burst=8)
        self._level_count: int = 0
        self._level_count_ts: float = 0.0
        self._level_count_lock: asyncio.Lock | None = None
        self._level_count_loop: asyncio.AbstractEventLoop | None = None

    def _get_level_count_lock(self) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        if self._level_count_lock is None or self._level_count_loop != loop:
            self._level_count_loop = loop
            self._level_count_lock = asyncio.Lock()
        return self._level_count_lock

    async def start(self) -> None:
        connector = aiohttp.TCPConnector(limit=8, ttl_dns_cache=300)
        self._session = aiohttp.ClientSession(
            base_url=self._base_url,
            connector=connector,
            timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
        )

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    async def _get(self, path: str, params: dict | None = None) -> Any:
        if self._session is None or self._session.closed:
            raise APIError("Client not started")
        await self._limiter.acquire()

        for attempt in range(2):
            try:
                async with self._session.get(path, params=params) as resp:
                    if resp.status == 429:
                        wait = float(resp.headers.get("Retry-After", 2))
                        logger.warning("Rate limited by API, sleeping %.1fs", wait)
                        await asyncio.sleep(min(wait, 10))
                        continue
                    if resp.status >= 500:
                        if attempt == 0:
                            await asyncio.sleep(1)
                            continue
                        raise APIError(f"{path}: HTTP {resp.status}")
                    if resp.status == 404:
                        raise APIError(f"not_found:{path}")
                    if resp.status != 200:
                        raise APIError(f"{path}: HTTP {resp.status}")

                    payload = await resp.json()
                    if not isinstance(payload, dict) or "data" not in payload:
                        raise APIError(f"{path}: invalid response payload")
                    return payload["data"]
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                if attempt == 0:
                    await asyncio.sleep(1)
                    continue
                raise APIError(f"{path}: {type(exc).__name__}") from exc

        raise APIError(f"{path}: all attempts failed")

    # ─── Users ────────────────────────────────────────────────────────────────

    async def get_user(self, username: str) -> dict | None:
        """Поиск пользователя по нику через leaderboard search."""
        if not username:
            return None
        data = await self._get("/leaderboard/user/list", {"search": username.strip(), "limit": 50})
        users = data.get("users") or []
        for u in users:
            if u.get("username", "").lower() == username.strip().lower():
                return u
        return users[0] if users else None

    async def get_user_by_id(self, user_id: int, username: str | None = None) -> dict | None:
        """Получает пользователя по ID."""
        if username:
            user = await self.get_user(username)
            if user and user.get("id") == user_id:
                return user
        # 1. Прямой запрос профиля по ID через /user/get
        try:
            data = await self._get("/user/get", {"id": user_id})
            if data and isinstance(data, dict) and data.get("username"):
                data["id"] = user_id
                return data
        except (APIError, aiohttp.ClientError, asyncio.TimeoutError) as exc:
            logger.debug("get_user_by_id via /user/get failed: %s", exc)

        # 2. Поиск в лидерборде (лимит максимум 50 по спецификации API)
        try:
            data = await self._get("/leaderboard/user/list", {"limit": 50})
            for u in data.get("users") or []:
                if u.get("id") == user_id:
                    return u
        except (APIError, aiohttp.ClientError, asyncio.TimeoutError) as exc:
            logger.debug("get_user_by_id via /leaderboard failed: %s", exc)

        return None

    async def get_records(self, user_id: int) -> list[dict]:
        """Безопасная пагинация рекордов с потолком MAX_PAGES."""
        records: list[dict] = []
        seen: set[int] = set()

        for page in range(MAX_PAGES):
            data = await self._get(
                "/user/record/list",
                {"user_id": user_id, "limit": PAGE_SIZE_RECORDS, "offset": page * PAGE_SIZE_RECORDS},
            )
            chunk: list = data.get("records") if isinstance(data, dict) else []
            if not isinstance(chunk, list):
                raise APIError("records is not a list")
            if not chunk:
                break

            ids = {r["id"] for r in chunk if isinstance(r, dict) and "id" in r}
            if ids & seen:
                logger.warning("Pagination loop for user %d at page %d — stopped", user_id, page)
                break
            seen.update(ids)
            records.extend(chunk)

            if len(chunk) < PAGE_SIZE_RECORDS:
                break
        else:
            logger.warning("Hit MAX_PAGES=%d for user %d", MAX_PAGES, user_id)

        return records

    async def get_country_position(self, user: dict) -> int | None:
        """Находит позицию игрока в топе его страны."""
        country = user.get("country")
        if not country:
            return None
        target_id = user.get("id")
        seen: set[int] = set()

        for page in range(MAX_PAGES):
            players = await self.get_leaderboard(offset=page * 25, limit=25, country=country)
            if not players:
                return None
            ids = {p.get("id") for p in players if isinstance(p, dict)}
            if ids & seen:
                break
            seen.update(ids)
            for i, p in enumerate(players, start=page * 25 + 1):
                if p.get("id") == target_id:
                    return i
            if len(players) < 25:
                return None

        return None

    # ─── Levels ───────────────────────────────────────────────────────────────

    async def get_level_info(self, level_id: int) -> dict | None:
        """Получает полную информацию об уровне по его ID."""
        try:
            data = await self._get("/level/classic/get", {"id": level_id})
            return data if isinstance(data, dict) else None
        except APIError as e:
            if "not_found" in str(e):
                return None
            raise

    async def search_level(self, name: str) -> dict | None:
        """Ищет уровень по названию (точное совпадение приоритетнее)."""
        data = await self._get("/level/classic/list", {"search": name.strip(), "limit": 25})
        levels = data.get("levels") or []
        for lvl in levels:
            if lvl.get("name", "").lower() == name.strip().lower():
                return lvl
        return levels[0] if levels else None

    async def search_levels(self, name: str, limit: int = 5) -> list[dict]:
        data = await self._get("/level/classic/list", {"search": name.strip(), "limit": limit})
        return data.get("levels") or []

    async def get_levels(self, offset: int = 0, limit: int = PAGE_SIZE_LEVELS) -> list[dict]:
        data = await self._get("/level/classic/list", {"limit": limit, "offset": offset})
        return data.get("levels") or []

    async def get_level_at_position(self, position: int) -> dict | None:
        """Получает уровень по позиции в списке топа (1-based)."""
        data = await self._get("/level/classic/list", {"limit": 1, "offset": position - 1})
        levels = data.get("levels") or []
        return levels[0] if levels else None

    async def get_level_victors_count(self, level_id: int) -> int:
        """Количество завершений уровня."""
        try:
            data = await self._get(
                "/level/classic/record/list", {"level_id": level_id, "limit": 1}
            )
            return data.get("completed_count", 0) if isinstance(data, dict) else 0
        except APIError:
            return 0

    async def get_level_victors(self, level_id: int, offset: int = 0, limit: int = 15) -> list[dict]:
        """Список завершивших уровень."""
        data = await self._get(
            "/level/classic/record/list",
            {"level_id": level_id, "limit": limit, "offset": offset, "status": "accepted"},
        )
        return data.get("records") or [] if isinstance(data, dict) else []

    async def get_level_count(self) -> int:
        """Бинарный поиск количества уровней с кэшированием на час."""
        async with self._get_level_count_lock():
            now = time.monotonic()
            if self._level_count > 0 and (now - self._level_count_ts) < 3600:
                return self._level_count

            async def _exists(offset: int) -> bool:
                try:
                    res = await self._get("/level/classic/list", {"limit": 1, "offset": offset})
                    return bool(res.get("levels"))
                except APIError:
                    return False

            if not await _exists(0):
                return 0

            low, high = 0, 1
            while await _exists(high):
                low, high = high, high * 2
                if high > 50000:
                    break

            while high - low > 1:
                mid = (low + high) // 2
                if await _exists(mid):
                    low = mid
                else:
                    high = mid

            self._level_count = high
            self._level_count_ts = now
            return self._level_count

    # ─── Leaderboards ─────────────────────────────────────────────────────────

    async def get_leaderboard(
        self, offset: int = 0, limit: int = 15, country: str | None = None
    ) -> list[dict]:
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if country:
            params["country"] = country
        data = await self._get("/leaderboard/user/list", params)
        return data.get("users") or [] if isinstance(data, dict) else []

    async def get_top_countries(self, offset: int = 0, limit: int = 25) -> list[dict]:
        """Эндпоинт стран Demonlist API требует type=main и возвращает весь список."""
        data = await self._get("/leaderboard/country/list", {"type": "main"})
        all_countries = data.get("countries") or [] if isinstance(data, dict) else []
        return all_countries[offset : offset + limit]


gdl = GDLClient()
