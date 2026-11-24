"""事件匯流排：後端只推事件，前端（WebSocket）訂閱（SDD 5.5）。"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Mapping, Protocol

logger = logging.getLogger(__name__)

QUEUE_SIZE = 200


class EventSink(Protocol):
    async def emit(self, event: Mapping[str, Any]) -> None: ...


def make_event(event_type: str, **fields: Any) -> dict[str, Any]:
    return {"type": event_type, **fields}


PROMPT_TYPES = frozenset({"confirm_request", "user_help_request", "waiting_quota"})


def _clears_prompts(event: Mapping[str, Any]) -> bool:
    """任務回到 running 或結束，代表待處理的請求都已處理完。"""
    return event.get("type") == "task_finished" or (
        event.get("type") == "task_status" and event.get("status") == "running")


class EventBus:
    """每個訂閱者一個佇列；佇列滿時丟掉最舊的事件，避免慢的連線拖住任務。

    也記住「還在等使用者處理」的事件，讓重新連線的前端能補看到（例如重新整理頁面時剛好有確認窗）。
    """

    def __init__(self) -> None:
        self._queues: frozenset[asyncio.Queue] = frozenset()
        self._prompts: tuple[dict[str, Any], ...] = ()

    def pending_prompts(self) -> tuple[dict[str, Any], ...]:
        return self._prompts

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_SIZE)
        self._queues = self._queues | {queue}
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._queues = self._queues - {queue}

    async def emit(self, event: Mapping[str, Any]) -> None:
        payload = dict(event)
        if payload.get("type") in PROMPT_TYPES:
            self._prompts = (*self._prompts, payload)
        elif _clears_prompts(payload):
            self._prompts = ()
        for queue in self._queues:
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:  # pragma: no cover — 競態保護
                    pass
                logger.warning("事件佇列已滿，丟棄最舊的事件")
            queue.put_nowait(payload)
