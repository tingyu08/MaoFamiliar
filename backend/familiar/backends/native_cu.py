"""native_cu 後端：使用 Gemini 內建 computer_use 工具（備選後端）。

原生動作會被翻譯成我們的 ActionKind 與參數格式，再走同一套驗證。
"""

from __future__ import annotations

from typing import Any, Callable, Mapping, Sequence

from familiar.backends.action_schema import companion_tools
from familiar.backends.parsing import RawCall, build_action, build_function_results, raw_call
from familiar.core.models import (
    Action,
    ActionKind,
    ActionOutcome,
    CallError,
    Environment,
    Frame,
    OperatingMode,
    SafetyDecision,
)

# 我們的執行器不支援、或沒有意義的原生動作；在工具設定中排除
UNSUPPORTED_NATIVE: tuple[str, ...] = (
    "triple_click", "middle_click", "mouse_down", "mouse_up", "move", "key_down", "key_up", "go_forward",
)

PIXELS_PER_NOTCH = 100

Args = Mapping[str, Any]


def _point(a: Args) -> dict[str, Any]:
    return {"x": a["x"], "y": a["y"]}


def _scroll(a: Args) -> dict[str, Any]:
    notches = max(1, min(10, round(float(a.get("magnitude_in_pixels", 300)) / PIXELS_PER_NOTCH)))
    return {**_point(a), "direction": a["direction"], "amount": notches}


_TRANSLATE: dict[str, tuple[ActionKind, Callable[[Args], dict[str, Any]]]] = {
    "click": (ActionKind.CLICK, _point),
    "double_click": (ActionKind.DOUBLE_CLICK, _point),
    "right_click": (ActionKind.RIGHT_CLICK, _point),
    "type": (ActionKind.TYPE_TEXT, lambda a: {"text": a["text"], "press_enter": a.get("press_enter", False)}),
    "press_key": (ActionKind.PRESS_KEYS, lambda a: {"keys": [a["key"]]}),
    "hotkey": (ActionKind.PRESS_KEYS, lambda a: {"keys": a["keys"]}),
    "drag_and_drop": (ActionKind.DRAG, lambda a: {"x1": a["start_x"], "y1": a["start_y"],
                                                   "x2": a["end_x"], "y2": a["end_y"]}),
    "scroll": (ActionKind.SCROLL, _scroll),
    "navigate": (ActionKind.NAVIGATE, lambda a: {"url": a["url"]}),
    "go_back": (ActionKind.GO_BACK, lambda a: {}),
    "wait": (ActionKind.WAIT, lambda a: {"seconds": a.get("seconds", 1)}),
    "take_screenshot": (ActionKind.WAIT, lambda a: {"seconds": 0}),
}


def _safety_of(arguments: Args) -> SafetyDecision | None:
    raw = arguments.get("safety_decision")
    if raw is None:
        return None
    if isinstance(raw, Mapping) and isinstance(raw.get("decision"), str):
        return SafetyDecision(raw["decision"], str(raw.get("explanation", "")))
    # 格式不明時保守處理：要求確認
    return SafetyDecision("require_confirmation", "無法解析的安全判定")


def _translate(call: RawCall, frame: Frame) -> Action | CallError:
    kind, convert = _TRANSLATE[call.name]
    try:
        arguments = {**convert(call.arguments), "intent": call.arguments.get("intent", "")}
    except KeyError as missing:
        return CallError(call.call_id, call.name, f"參數錯誤：缺少 {missing.args[0]}")
    return build_action(kind.value, call, arguments, frame, safety=_safety_of(call.arguments))


class NativeCuBackend:
    name = "native_cu"

    def tools(self, env: Environment, mode: OperatingMode) -> list[dict[str, Any]]:
        if mode is OperatingMode.REALTIME:
            raise ValueError("原生 computer_use 不支援即時模式，請使用 custom_fc")
        computer_use = {
            "type": "computer_use",
            "environment": env.value,
            "enable_prompt_injection_detection": True,
            "excluded_predefined_functions": list(UNSUPPORTED_NATIVE),
        }
        return [computer_use, *companion_tools(env)]

    def parse(self, calls: Sequence[Any], frame: Frame) -> list[Action | CallError]:
        parsed: list[Action | CallError] = []
        for obj in calls:
            call = raw_call(obj)
            if call.name in _TRANSLATE:
                parsed.append(_translate(call, frame))
            elif call.name in UNSUPPORTED_NATIVE:
                parsed.append(CallError(call.call_id, call.name, f"不支援的動作：{call.name}，請改用其他動作"))
            else:
                parsed.append(build_action(call.name, call, call.arguments, frame))
        return parsed

    def build_results(self, outcomes: Sequence[ActionOutcome]) -> list[dict[str, Any]]:
        return build_function_results(outcomes)
