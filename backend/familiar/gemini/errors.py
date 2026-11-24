"""把 Gemini SDK 的例外分類成路由器能處理的種類（依第 0 週實際看到的錯誤訊息）。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum

MAX_MESSAGE_CHARS = 500
_RETRY_RE = re.compile(r"retry in\s+(?:(\d+)h)?\s*(?:(\d+)m)?\s*(?:(\d+(?:\.\d+)?)s)?", re.IGNORECASE)


class ErrorKind(StrEnum):
    DAILY_QUOTA = "daily_quota"        # 429 每日上限 → 今日停用該模型
    MINUTE_QUOTA = "minute_quota"      # 429 每分鐘上限 → 短暫等待
    OVERLOADED = "overloaded"          # 5xx（常見 503 high demand）→ 重試、冷卻
    TIMEOUT = "timeout"                # 用戶端逾時 → 視同過載
    SAFETY_BLOCKED = "safety_blocked"  # 400 安全分類器擋下 → 交給呼叫端（可改用 custom_fc）
    BAD_REQUEST = "bad_request"        # 其他 400 → 程式錯誤，不重試
    OTHER = "other"


@dataclass(frozen=True)
class ApiError:
    kind: ErrorKind
    message: str
    status: int | None = None
    retry_after: timedelta | None = None


def parse_retry_after(text: str) -> timedelta | None:
    """解析「Please retry in 59m51s」這類提示。"""
    match = _RETRY_RE.search(text)
    if not match or not any(match.groups()):
        return None
    hours, minutes, seconds = match.groups()
    return timedelta(hours=int(hours or 0), minutes=int(minutes or 0), seconds=float(seconds or 0))


def _status_of(exc: BaseException) -> int | None:
    for attr in ("status_code", "code", "status"):
        value = getattr(exc, attr, None)
        if isinstance(value, int):
            return value
    status = getattr(getattr(exc, "response", None), "status_code", None)
    return status if isinstance(status, int) else None


def _kind_for(status: int | None, lowered: str, exc: BaseException) -> ErrorKind:
    if "timeout" in type(exc).__name__.lower():
        return ErrorKind.TIMEOUT
    if status == 429:
        return ErrorKind.DAILY_QUOTA if "per day" in lowered else ErrorKind.MINUTE_QUOTA
    if status is not None and 500 <= status < 600:
        return ErrorKind.OVERLOADED
    if status == 400:
        return ErrorKind.SAFETY_BLOCKED if "safety" in lowered else ErrorKind.BAD_REQUEST
    return ErrorKind.OTHER


def classify(exc: BaseException) -> ApiError:
    message = str(exc)
    status = _status_of(exc)
    return ApiError(
        kind=_kind_for(status, message.lower(), exc),
        message=message[:MAX_MESSAGE_CHARS],
        status=status,
        retry_after=parse_retry_after(message),
    )
