"""技術驗證共用工具：讀設定、建立 client、呼叫計時、錯誤分類、結果存檔。"""

from __future__ import annotations

import base64
import json
import os
import sys
import time
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from dotenv import load_dotenv

ROOT = Path(__file__).parent
RESULTS_DIR = ROOT / "results"
FIXTURES_DIR = ROOT / "fixtures" / "generated"

AGENT_MODELS: tuple[str, ...] = (
    "gemini-4-hydrogen",
    "gemini-3.8-flash",
    "gemini-3.6-flash",
    "gemini-3.5-flash-lite",
)
DECISION_MODEL = "gemini-decision-1"
DEFAULT_DELAY_SEC = 6.0  # 免費版 RPM 未知，保守間隔
MAX_ERROR_CHARS = 400
REQUEST_TIMEOUT_MS = 90_000  # 單一請求最多等 90 秒
# 不讓 SDK 自動重試：驗證要看到第一次失敗的原始錯誤（例如 429），而不是被重試蓋掉
HTTP_OPTIONS = {"timeout": REQUEST_TIMEOUT_MS, "retry_options": {"attempts": 1}}


@dataclass(frozen=True)
class CallOutcome:
    """一次 API 呼叫的結果（不可變）。"""

    ok: bool
    latency_ms: int
    value: Any = None
    error_type: str | None = None
    error_message: str | None = None
    http_status: int | None = None
    attempts: int = 1


@dataclass(frozen=True)
class RunMeta:
    script: str
    started_at: str
    sdk_version: str
    extra: dict[str, Any] = field(default_factory=dict)


def require_api_key() -> str:
    load_dotenv(ROOT / ".env")
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        sys.exit(
            "找不到 GEMINI_API_KEY。請把 spikes/.env.example 複製成 spikes/.env 並填入免費版 API key。"
        )
    return key


def make_clients() -> tuple[Any, Any]:
    """回傳 (interactions 用 client, decisions 用 v1beta client)。"""
    from google import genai

    key = require_api_key()
    agent_client = genai.Client(api_key=key, http_options=HTTP_OPTIONS)
    decision_client = genai.Client(api_key=key, http_options={**HTTP_OPTIONS, "api_version": "v1beta"})
    return agent_client, decision_client


def sdk_version() -> str:
    from importlib.metadata import version

    return version("google-genai")


def image_block(path: Path) -> dict[str, str]:
    data = base64.b64encode(path.read_bytes()).decode("ascii")
    return {"type": "image", "data": data, "mime_type": "image/png"}


def _status_of(exc: Exception) -> int | None:
    for attr in ("status_code", "code", "status"):
        value = getattr(exc, attr, None)
        if isinstance(value, int):
            return value
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    return status if isinstance(status, int) else None


OVERLOAD_RETRIES = 3
OVERLOAD_BACKOFF_SEC = (10.0, 20.0, 40.0)


def is_overloaded(outcome: CallOutcome) -> bool:
    return outcome.http_status == 503 or "high demand" in (outcome.error_message or "")


def timed_call(fn: Callable[[], Any], label: str = "", retries: int = OVERLOAD_RETRIES) -> CallOutcome:
    """呼叫 API；遇到 503（模型過載）時退避重試，並記錄總共嘗試幾次。"""
    outcome = _single_call(fn, label)
    for attempt in range(retries):
        if not is_overloaded(outcome):
            break
        wait = OVERLOAD_BACKOFF_SEC[min(attempt, len(OVERLOAD_BACKOFF_SEC) - 1)]
        print(f"   503 過載，{wait:.0f} 秒後重試（第 {attempt + 2} 次）", flush=True)
        time.sleep(wait)
        retried = _single_call(fn, label)
        outcome = replace(retried, attempts=attempt + 2)
    return outcome


def _single_call(fn: Callable[[], Any], label: str) -> CallOutcome:
    """執行一次 API 呼叫並把例外轉成 CallOutcome，立即印出進度。"""
    print(f"→ {label} …", end=" ", flush=True)
    start = time.perf_counter()
    try:
        value = fn()
    except Exception as exc:  # noqa: BLE001 — 驗證腳本要記錄所有失敗類型
        outcome = CallOutcome(
            ok=False,
            latency_ms=int((time.perf_counter() - start) * 1000),
            error_type=type(exc).__name__,
            error_message=str(exc)[:MAX_ERROR_CHARS],
            http_status=_status_of(exc),
        )
        print(f"失敗 {outcome.latency_ms} ms：{outcome.error_type} {outcome.error_message[:120]}", flush=True)
        return outcome
    outcome = CallOutcome(ok=True, latency_ms=int((time.perf_counter() - start) * 1000), value=value)
    print(f"成功 {outcome.latency_ms} ms", flush=True)
    return outcome


def usage_of(interaction: Any) -> dict[str, int | None]:
    usage = getattr(interaction, "usage", None)
    if usage is None:
        return {"input": None, "output": None, "total": None}
    return {
        "input": getattr(usage, "total_input_tokens", None),
        "output": getattr(usage, "total_output_tokens", None),
        "total": getattr(usage, "total_tokens", None),
    }


def function_calls_of(interaction: Any) -> list[dict[str, Any]]:
    steps = getattr(interaction, "steps", None) or []
    return [
        {"id": s.id, "name": s.name, "arguments": dict(s.arguments)}
        for s in steps
        if getattr(s, "type", None) == "function_call"
    ]


def save_results(name: str, meta: RunMeta, rows: list[dict[str, Any]]) -> Path:
    RESULTS_DIR.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = RESULTS_DIR / f"{name}-{stamp}.json"
    payload = {"meta": asdict(meta), "rows": rows}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return path


def new_meta(script: str, **extra: Any) -> RunMeta:
    return RunMeta(
        script=script,
        started_at=datetime.now().isoformat(timespec="seconds"),
        sdk_version=sdk_version(),
        extra=dict(extra),
    )


def pause(seconds: float) -> None:
    if seconds > 0:
        time.sleep(seconds)
