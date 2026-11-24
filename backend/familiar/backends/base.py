"""動作後端介面：決定給模型哪些工具、怎麼解析模型的函式呼叫、怎麼回傳結果。"""

from __future__ import annotations

from typing import Any, Protocol, Sequence, runtime_checkable

from familiar.core.models import Action, ActionOutcome, CallError, Environment, Frame, OperatingMode


@runtime_checkable
class ActionBackend(Protocol):
    name: str

    def tools(self, env: Environment, mode: OperatingMode) -> list[dict[str, Any]]: ...

    def parse(self, calls: Sequence[Any], frame: Frame) -> list[Action | CallError]: ...

    def build_results(self, outcomes: Sequence[ActionOutcome]) -> list[dict[str, Any]]: ...
