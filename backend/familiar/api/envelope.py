"""統一回應格式：{success, data, error, meta}。"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from enum import Enum
from typing import Any

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


def to_jsonable(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return to_jsonable(asdict(value))
    if isinstance(value, dict):
        return {str(k): to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_jsonable(v) for v in value]
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def ok(data: Any = None, meta: dict[str, Any] | None = None, status_code: int = 200) -> JSONResponse:
    body: dict[str, Any] = {"success": True, "data": to_jsonable(data), "error": None}
    if meta is not None:
        body["meta"] = meta
    return JSONResponse(body, status_code=status_code)


def fail(message: str, status_code: int) -> JSONResponse:
    return JSONResponse({"success": False, "data": None, "error": message}, status_code=status_code)


async def http_error_handler(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
    return fail(str(exc.detail), exc.status_code)


async def validation_error_handler(_request: Request, exc: RequestValidationError) -> JSONResponse:
    fields = "、".join(".".join(str(p) for p in e["loc"][1:]) or "內容" for e in exc.errors())
    return fail(f"輸入資料格式錯誤：{fields}", 422)
