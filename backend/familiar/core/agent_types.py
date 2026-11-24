"""代理人迴圈用到的設定、輸入輸出與相依物件型別。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, Awaitable, Callable

if TYPE_CHECKING:
    from familiar.knowledge.completion_check import CompletionChecker
    from familiar.knowledge.decision_actor import DecisionActor
    from familiar.knowledge.reflex import ReflexDecider

from familiar.backends.base import ActionBackend
from familiar.core.control import TaskControl
from familiar.core.events import EventSink
from familiar.core.models import Environment, OperatingMode
from familiar.core.safety import SafetyConfig
from familiar.core.tasks import TaskStatus
from familiar.executors.base import Executor
from familiar.gemini.interactions import AgentModelClient
from familiar.gemini.model_router import ModelRouter


@dataclass(frozen=True)
class LoopConfig:
    max_steps: int = 80
    max_minutes: float = 30
    confirm_timeout_sec: float = 300
    stuck_warn_steps: int = 5
    stuck_stop_steps: int = 8
    repeat_warn_actions: int = 4  # 連續做同樣的動作幾次就提醒模型換做法（畫面有小變化時卡住偵測抓不到）
    compress_after_steps: int = 20
    model_screenshot_max_side: int = 1280
    est_tokens_per_turn: int = 2500
    max_quota_wait_sec: float = 3600


@dataclass(frozen=True)
class TaskSpec:
    task_id: str
    instruction: str
    environment: Environment
    mode: OperatingMode = OperatingMode.UI


@dataclass(frozen=True)
class LoopResult:
    status: TaskStatus
    text: str | None = None
    error: str | None = None
    steps: int = 0


@dataclass(frozen=True)
class StepInfo:
    """一個步驟的完整紀錄（給 TaskRunner 存檔）。"""

    index: int
    layer: str
    model: str | None
    action: dict[str, Any]
    ok: bool
    message: str
    safety: dict[str, Any] | None
    screenshot_png: bytes
    phash: str
    duration_ms: int
    tokens_in: int = 0
    tokens_out: int = 0
    locator: dict[str, Any] | None = None  # 技能錄製用
    page_url: str | None = None            # 動作前的網址
    decision_calls: int = 0                # 這一步用掉幾次 Decisions API


@dataclass(frozen=True)
class LoopDeps:
    router: ModelRouter
    client: AgentModelClient
    backend: ActionBackend
    executor: Executor
    events: EventSink
    control: TaskControl
    safety: SafetyConfig
    clock: Callable[[], datetime]
    on_step: Callable[[StepInfo], Awaitable[None]]
    on_status: Callable[[TaskStatus], Awaitable[None]]
    actor: "DecisionActor | None" = None  # 第 3 層；None 表示停用
    completion: "CompletionChecker | None" = None  # 完成檢查；None 表示停用
    reflex: "ReflexDecider | None" = None  # 反射層（遊戲模式的 set_reflex）；None 表示停用
