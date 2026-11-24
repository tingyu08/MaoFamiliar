"""WebSocket（SDD 5.5）：連線後第一則訊息必須是 auth；之後推送事件、接收使用者回應。"""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ValidationError

from familiar.api.context import ServerSettings, Services
from familiar.api.envelope import to_jsonable

logger = logging.getLogger(__name__)

AUTH_TIMEOUT_SEC = 5
CLOSE_UNAUTHORIZED = 4401
CLOSE_FORBIDDEN_ORIGIN = 4403

router = APIRouter()


class ConfirmResponse(BaseModel):
    type: str
    request_id: str
    approved: bool


class HelpDone(BaseModel):
    type: str
    request_id: str


class StopRequest(BaseModel):
    type: str
    task_id: str


async def _authenticate(ws: WebSocket, settings: ServerSettings) -> bool:
    try:
        first = await asyncio.wait_for(ws.receive_json(), timeout=AUTH_TIMEOUT_SEC)
    except (asyncio.TimeoutError, json.JSONDecodeError, WebSocketDisconnect):
        return False
    token = first.get("token") if isinstance(first, dict) and first.get("type") == "auth" else None
    return isinstance(token, str) and secrets.compare_digest(token, settings.token)


def _handle_message(message: Any, services: Services) -> dict[str, Any]:
    """處理前端送來的一則訊息，回傳要回覆給這個連線的確認訊息。"""
    kind = message.get("type") if isinstance(message, dict) else None
    try:
        if kind == "confirm_response":
            body = ConfirmResponse.model_validate(message)
            return {"type": "ack", "for": kind, "ok": services.runner.confirm(body.request_id, body.approved)}
        if kind == "user_help_done":
            body = HelpDone.model_validate(message)
            return {"type": "ack", "for": kind, "ok": services.runner.user_help_done(body.request_id)}
        if kind == "stop":
            body = StopRequest.model_validate(message)
            return {"type": "ack", "for": kind, "ok": services.runner.stop(body.task_id)}
    except ValidationError:
        return {"type": "error", "message": f"訊息格式錯誤：{kind}"}
    return {"type": "error", "message": "不認得的訊息類型"}


async def _pump_events(ws: WebSocket, queue: asyncio.Queue) -> None:
    while True:
        event = await queue.get()
        await ws.send_json(to_jsonable(event))


@router.websocket("/ws")
async def events_socket(ws: WebSocket) -> None:
    services: Services = ws.app.state.services
    settings: ServerSettings = ws.app.state.settings
    origin = ws.headers.get("origin")
    if origin is not None and origin not in settings.allowed_origins:
        await ws.close(code=CLOSE_FORBIDDEN_ORIGIN)
        return
    await ws.accept()
    if not await _authenticate(ws, settings):
        await ws.close(code=CLOSE_UNAUTHORIZED)
        return
    queue = services.events.subscribe()
    pump = asyncio.create_task(_pump_events(ws, queue))
    try:
        await ws.send_json(to_jsonable({"type": "hello", "current_task_id": services.runner.current_task_id,
                                        "pending": services.events.pending_prompts()}))
        while True:
            message = await ws.receive_json()
            await ws.send_json(_handle_message(message, services))
    except (WebSocketDisconnect, json.JSONDecodeError):
        pass
    finally:
        pump.cancel()
        services.events.unsubscribe(queue)
