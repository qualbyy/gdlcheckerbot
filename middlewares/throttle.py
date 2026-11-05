"""
Throttle middleware — ограничивает спам-запросы.
1 действие в 1.5 секунды на пользователя. Молча дропаем лишние.
"""

import time
from collections import defaultdict
from typing import Any, Callable, Awaitable

from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery


class ThrottleMiddleware(BaseMiddleware):
    _MAX_ENTRIES = 10_000

    def __init__(self, rate: float = 1.5) -> None:
        self._rate = rate
        self._last: dict[int, float] = {}

    async def __call__(
        self,
        handler: Callable[[Any, dict], Awaitable[Any]],
        event: Any,
        data: dict,
    ) -> Any:
        user_id: int | None = None

        if isinstance(event, Message) and event.from_user:
            user_id = event.from_user.id
        elif isinstance(event, CallbackQuery) and event.from_user:
            user_id = event.from_user.id

        if user_id is not None:
            now = time.monotonic()
            last_time = self._last.get(user_id, 0.0)
            if now - last_time < self._rate:
                if isinstance(event, CallbackQuery):
                    await event.answer("Слишком частые запросы.", show_alert=False)
                return  # дропаем молча
            self._last[user_id] = now

            # Защита от бесконечного роста памяти (TTL чистка)
            if len(self._last) > self._MAX_ENTRIES:
                cutoff = now - max(self._rate * 10, 60.0)
                self._last = {uid: t for uid, t in self._last.items() if t > cutoff}

        return await handler(event, data)
