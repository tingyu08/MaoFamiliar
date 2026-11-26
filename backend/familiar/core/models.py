"""核心資料型別。全部不可變：要改就產生新物件。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Mapping


class Environment(StrEnum):
    BROWSER = "browser"
    DESKTOP = "desktop"


class OperatingMode(StrEnum):
    UI = "ui"
    REALTIME = "realtime"


class Risk(StrEnum):
    LOW = "low"
    HIGH = "high"


class ActionKind(StrEnum):
    # 指標
    CLICK = "click"
    DOUBLE_CLICK = "double_click"
    RIGHT_CLICK = "right_click"
    DRAG = "drag"
    SCROLL = "scroll"
    # 鍵盤
    TYPE_TEXT = "type_text"
    PRESS_KEYS = "press_keys"
    # 即時模式
    HOLD_KEYS = "hold_keys"
    LOOK = "look"
    HOLD_MOUSE = "hold_mouse"
    TAP_KEYS = "tap_keys"
    SET_REFLEX = "set_reflex"  # 把快速反應交給反射層（Decisions 看畫面 → 照規則按鍵）
    # 瀏覽器
    NAVIGATE = "navigate"
    GO_BACK = "go_back"
    CLICK_ELEMENT = "click_element"
    FILL_ELEMENT = "fill_element"
    # 觀察（只看不動）
    ZOOM = "zoom"  # 放大畫面上的一塊區域，看清楚小字、小圖示
    # 流程
    WAIT = "wait"
    ASK_USER = "ask_user"
    TASK_DONE = "task_done"
    # 規劃
    COMPLETE_SUBGOAL = "complete_subgoal"
    REVISE_PLAN = "revise_plan"
    # 知識
    REGISTER_STATE = "register_state"
    SAVE_NOTE = "save_note"
    USE_SKILL = "use_skill"
    REQUEST_MODE = "request_mode"


# 會對目標造成影響、需要評估風險的動作
IMPACTFUL_KINDS: frozenset[ActionKind] = frozenset({
    ActionKind.CLICK, ActionKind.DOUBLE_CLICK, ActionKind.RIGHT_CLICK, ActionKind.DRAG,
    ActionKind.TYPE_TEXT, ActionKind.PRESS_KEYS, ActionKind.NAVIGATE,
    ActionKind.CLICK_ELEMENT, ActionKind.FILL_ELEMENT,
})

TEXT_INPUT_KINDS: frozenset[ActionKind] = frozenset({ActionKind.TYPE_TEXT, ActionKind.FILL_ELEMENT})


def _freeze(value: Any) -> Any:
    """把 list／dict 轉成 tuple／唯讀 mapping，避免共用可變物件。"""
    if isinstance(value, Mapping):
        return MappingProxyType({k: _freeze(v) for k, v in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(v) for v in value)
    return value


def thaw(value: Any) -> Any:
    """_freeze 的反向：轉回一般 dict／list（新物件），給 JSON 序列化用。"""
    if isinstance(value, Mapping):
        return {k: thaw(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [thaw(v) for v in value]
    return value


@dataclass(frozen=True)
class Point:
    x: int
    y: int


@dataclass(frozen=True)
class Frame:
    """座標換算的基準：截圖（或視窗客戶區）的像素大小。"""

    width: int
    height: int

    def __post_init__(self) -> None:
        if self.width <= 0 or self.height <= 0:
            raise ValueError(f"畫面大小必須為正數：{self.width}×{self.height}")


@dataclass(frozen=True)
class SafetyDecision:
    """原生 computer_use 回傳的安全判定。"""

    decision: str
    explanation: str = ""

    @property
    def requires_confirmation(self) -> bool:
        return self.decision == "require_confirmation"


@dataclass(frozen=True, eq=True)
class Action:
    """一個待執行的動作。座標類參數已換算成像素。"""

    kind: ActionKind
    args: Mapping[str, Any]
    intent: str = ""
    call_id: str | None = None
    risk: Risk = Risk.LOW
    safety: SafetyDecision | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "args", _freeze(dict(self.args)))

    __hash__ = None  # type: ignore[assignment]  # args 是 mapping，不可雜湊


@dataclass(frozen=True)
class CallError:
    """模型的函式呼叫無法解析或參數不合法；會以錯誤結果回傳給模型。"""

    call_id: str | None
    name: str
    message: str


@dataclass(frozen=True)
class ActionOutcome:
    """動作執行結果，用來組成回傳給模型的 function_result。"""

    call_id: str | None
    name: str
    ok: bool
    message: str = ""
    data: Mapping[str, Any] = field(default_factory=dict)
    screenshot_png: bytes | None = None
    safety_acknowledged: bool = False
    zoom_png: bytes | None = None  # zoom 的放大圖；排在 screenshot_png 之前送給模型

    def __post_init__(self) -> None:
        object.__setattr__(self, "data", _freeze(dict(self.data)))

    __hash__ = None  # type: ignore[assignment]
