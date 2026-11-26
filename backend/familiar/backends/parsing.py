"""兩種動作後端共用：把模型的函式呼叫轉成 Action，以及組出回傳給模型的 function_result。"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from pydantic import ValidationError

from familiar.backends.action_args import ARG_MODELS, POINT_FIELDS
from familiar.core.coords import to_pixel
from familiar.core.models import Action, ActionKind, ActionOutcome, CallError, Frame, Risk, SafetyDecision

_CONTROL_FIELDS = frozenset({"intent", "risk"})


@dataclass(frozen=True)
class RawCall:
    call_id: str | None
    name: str
    arguments: Mapping[str, Any]


def raw_call(obj: Any) -> RawCall:
    """接受 dict 或 SDK 的 FunctionCallStep 物件。"""
    if isinstance(obj, Mapping):
        return RawCall(obj.get("id"), str(obj.get("name", "")), dict(obj.get("arguments") or {}))
    return RawCall(getattr(obj, "id", None), str(getattr(obj, "name", "")), dict(getattr(obj, "arguments", {}) or {}))


def _format_validation(error: ValidationError) -> str:
    parts = [f"{'.'.join(str(p) for p in e['loc']) or '參數'}：{e['msg']}" for e in error.errors()]
    return "參數錯誤：" + "；".join(parts)


def _to_pixels(kind: ActionKind, values: dict[str, Any], frame: Frame) -> dict[str, Any]:
    converted = dict(values)
    for fx, fy in POINT_FIELDS.get(kind, ()):
        point = to_pixel(values[fx], values[fy], frame)
        converted = {**converted, fx: point.x, fy: point.y}
    return converted


def build_action(
    kind_name: str,
    call: RawCall,
    arguments: Mapping[str, Any],
    frame: Frame,
    safety: SafetyDecision | None = None,
) -> Action | CallError:
    """驗證參數並建立 Action；任何問題都回傳 CallError（之後回報給模型）。"""
    try:
        kind = ActionKind(kind_name)
    except ValueError:
        return CallError(call.call_id, call.name, f"未知的動作：{call.name}")
    try:
        parsed = ARG_MODELS[kind].model_validate(dict(arguments))
    except ValidationError as error:
        return CallError(call.call_id, call.name, _format_validation(error))
    values = parsed.model_dump(mode="json", exclude=_CONTROL_FIELDS)
    return Action(
        kind=kind,
        args=_to_pixels(kind, values, frame),
        intent=parsed.intent,
        call_id=call.call_id,
        risk=getattr(parsed, "risk", Risk.LOW),
        safety=safety,
    )


def _text_part(outcome: ActionOutcome) -> dict[str, str]:
    payload: dict[str, Any] = {"ok": outcome.ok, **dict(outcome.data)}
    if not outcome.ok:
        payload["error"] = outcome.message
    elif outcome.message:
        payload["message"] = outcome.message
    if outcome.safety_acknowledged:
        payload["safety_acknowledgement"] = True
    return {"type": "text", "text": json.dumps(payload, ensure_ascii=False, default=str)}


def _image_part(png: bytes) -> dict[str, str]:
    return {"type": "image", "mime_type": "image/png", "data": base64.b64encode(png).decode("ascii")}


def build_function_results(outcomes: Sequence[ActionOutcome]) -> list[dict[str, Any]]:
    results = []
    for outcome in outcomes:
        images = tuple(png for png in (outcome.zoom_png, outcome.screenshot_png) if png)
        parts: list[dict[str, str]] = [_text_part(outcome), *(_image_part(png) for png in images)]
        step: dict[str, Any] = {"type": "function_result", "call_id": outcome.call_id,
                                "name": outcome.name, "result": parts}
        if not outcome.ok:
            step["is_error"] = True
        results.append(step)
    return results
