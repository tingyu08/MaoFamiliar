"""custom_fc 後端：只用我們自己定義的函式，不依賴原生 computer_use（預設後端，SRS FR-101）。"""

from __future__ import annotations

from typing import Any, Sequence

from familiar.backends.action_schema import custom_tools
from familiar.backends.parsing import build_action, build_function_results, raw_call
from familiar.core.models import Action, ActionOutcome, CallError, Environment, Frame, OperatingMode


class CustomFcBackend:
    name = "custom_fc"

    def tools(self, env: Environment, mode: OperatingMode) -> list[dict[str, Any]]:
        return list(custom_tools(env, mode))

    def parse(self, calls: Sequence[Any], frame: Frame) -> list[Action | CallError]:
        parsed = []
        for obj in calls:
            call = raw_call(obj)
            parsed.append(build_action(call.name, call, call.arguments, frame))
        return parsed

    def build_results(self, outcomes: Sequence[ActionOutcome]) -> list[dict[str, Any]]:
        return build_function_results(outcomes)
