"""任務控制：停止、等待使用者確認、等待使用者協助。全部可被「停止」打斷。"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any


class TaskControl:
    def __init__(self) -> None:
        self._stop = asyncio.Event()
        self._pending: dict[str, asyncio.Future] = {}

    @property
    def stopped(self) -> bool:
        return self._stop.is_set()

    def request_stop(self) -> None:
        self._stop.set()
        for future in self._pending.values():
            if not future.done():
                future.cancel()

    @staticmethod
    def new_request_id() -> str:
        return uuid.uuid4().hex[:12]

    async def _wait(self, request_id: str, timeout: float | None) -> Any:
        """等某個請求被回應；逾時或被停止時回傳 None。"""
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending = {**self._pending, request_id: future}
        try:
            if self.stopped:
                return None
            return await asyncio.wait_for(asyncio.shield(future), timeout)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            if self.stopped:
                return None
            if future.cancelled():
                return None
            return None
        finally:
            self._pending = {k: v for k, v in self._pending.items() if k != request_id}

    async def wait_confirmation(self, request_id: str, timeout: float) -> bool:
        return bool(await self._wait(request_id, timeout))

    async def wait_user_help(self, request_id: str, timeout: float | None = None) -> bool:
        return bool(await self._wait(request_id, timeout))

    def _resolve(self, request_id: str, value: Any) -> bool:
        future = self._pending.get(request_id)
        if future is None or future.done():
            return False
        future.set_result(value)
        return True

    def resolve_confirmation(self, request_id: str, approved: bool) -> bool:
        return self._resolve(request_id, approved)

    def resolve_user_help(self, request_id: str) -> bool:
        return self._resolve(request_id, True)

    async def sleep_or_stop(self, seconds: float) -> bool:
        """睡指定秒數；中途被停止就提早回傳 True。"""
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=max(0.0, seconds))
        except asyncio.TimeoutError:
            return False
        return True
