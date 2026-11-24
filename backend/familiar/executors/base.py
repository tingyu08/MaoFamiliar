"""執行器介面：代理人迴圈只依賴這個介面，瀏覽器與桌面可互換（SDD 3.2）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, runtime_checkable

from familiar.core.models import Action, Environment, Frame
from familiar.executors.elements import ElementTable


@dataclass(frozen=True)
class Screenshot:
    png: bytes
    frame: Frame


@dataclass(frozen=True)
class ExecResult:
    ok: bool
    message: str = ""
    data: Mapping[str, Any] = field(default_factory=dict)  # 會回傳給模型
    locator: Mapping[str, Any] | None = None  # 只給技能錄製用，不回傳給模型


# 會作用在某個元素上、需要記錄定位資訊的動作
ELEMENT_KINDS = frozenset({"click", "double_click", "right_click", "click_element", "fill_element"})


@runtime_checkable
class Executor(Protocol):
    environment: Environment

    async def start(self, target: str) -> None: ...

    async def screenshot(self) -> Screenshot: ...

    async def execute(self, action: Action) -> ExecResult: ...

    async def context_info(self) -> dict[str, str]: ...

    async def elements(self) -> ElementTable | None: ...

    async def is_password_target(self, action: Action) -> bool: ...

    async def ref_for_css(self, css: str) -> str | None: ...

    async def element_at(self, x: float, y: float) -> Mapping[str, Any] | None: ...

    async def close(self) -> None: ...
