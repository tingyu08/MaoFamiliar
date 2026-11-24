"""每種動作的參數 schema（系統邊界驗證：模型輸出一律視為不可信）。"""

from __future__ import annotations

from typing import Annotated, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from familiar.core.models import ActionKind, OperatingMode, Risk

MAX_ACTION_SECONDS = 5.0  # SRS FR-803
MAX_WAIT_SECONDS = 30.0
MAX_TEXT = 2000

Coord = Annotated[float, Field(ge=0, le=1000)]
Key = Annotated[str, StringConstraints(strip_whitespace=True, to_lower=True, min_length=1, max_length=20)]
Keys = Annotated[list[Key], Field(min_length=1, max_length=5)]
Ref = Annotated[str, Field(pattern=r"^e\d+$")]
ShortText = Annotated[str, Field(min_length=1, max_length=500)]
HoldSeconds = Annotated[float, Field(gt=0, le=MAX_ACTION_SECONDS)]


class _Base(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")
    intent: str = Field(default="", max_length=300)


class _Impactful(_Base):
    risk: Risk = Risk.LOW


class PointArgs(_Impactful):
    x: Coord
    y: Coord


class DragArgs(_Impactful):
    x1: Coord
    y1: Coord
    x2: Coord
    y2: Coord


class ScrollArgs(_Base):
    x: Coord
    y: Coord
    direction: Literal["up", "down", "left", "right"]
    amount: int = Field(default=3, ge=1, le=10)


class TypeTextArgs(_Impactful):
    text: str = Field(min_length=1, max_length=MAX_TEXT)
    press_enter: bool = False


class PressKeysArgs(_Impactful):
    keys: Keys


class HoldKeysArgs(_Base):
    keys: Keys
    seconds: HoldSeconds


class LookArgs(_Base):
    dx: float = Field(ge=-180, le=180)
    dy: float = Field(ge=-90, le=90)


class HoldMouseArgs(_Base):
    button: Literal["left", "right", "middle"] = "left"
    seconds: HoldSeconds


class TapKeysArgs(_Base):
    keys: Keys
    times: int = Field(default=1, ge=1, le=20)


class NavigateArgs(_Impactful):
    url: str = Field(min_length=1, max_length=2048)

    @field_validator("url")
    @classmethod
    def _http_only(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError("只接受 http(s) 網址")
        return value


class GoBackArgs(_Base):
    pass


class ClickElementArgs(_Impactful):
    ref: Ref


class FillElementArgs(_Impactful):
    ref: Ref
    text: str = Field(min_length=1, max_length=MAX_TEXT)


class WaitArgs(_Base):
    seconds: float = Field(default=1, ge=0, le=MAX_WAIT_SECONDS)


class AskUserArgs(_Base):
    reason: ShortText


class TaskDoneArgs(_Base):
    summary: str = Field(min_length=1, max_length=MAX_TEXT)


class CompleteSubgoalArgs(_Base):
    id: str = Field(min_length=1, max_length=50)
    summary: str = Field(default="", max_length=500)


class RevisePlanArgs(_Base):
    subgoals: list[Annotated[str, Field(min_length=1, max_length=200)]] = Field(min_length=1, max_length=20)


class RegisterStateArgs(_Base):
    name: str = Field(min_length=1, max_length=60)
    description: str = Field(min_length=1, max_length=300)


class SaveNoteArgs(_Base):
    text: ShortText


class UseSkillArgs(_Base):
    name: str = Field(min_length=1, max_length=60)


class RequestModeArgs(_Base):
    mode: OperatingMode
    reason: str = Field(default="", max_length=300)


ARG_MODELS: dict[ActionKind, type[_Base]] = {
    ActionKind.CLICK: PointArgs,
    ActionKind.DOUBLE_CLICK: PointArgs,
    ActionKind.RIGHT_CLICK: PointArgs,
    ActionKind.DRAG: DragArgs,
    ActionKind.SCROLL: ScrollArgs,
    ActionKind.TYPE_TEXT: TypeTextArgs,
    ActionKind.PRESS_KEYS: PressKeysArgs,
    ActionKind.HOLD_KEYS: HoldKeysArgs,
    ActionKind.LOOK: LookArgs,
    ActionKind.HOLD_MOUSE: HoldMouseArgs,
    ActionKind.TAP_KEYS: TapKeysArgs,
    ActionKind.NAVIGATE: NavigateArgs,
    ActionKind.GO_BACK: GoBackArgs,
    ActionKind.CLICK_ELEMENT: ClickElementArgs,
    ActionKind.FILL_ELEMENT: FillElementArgs,
    ActionKind.WAIT: WaitArgs,
    ActionKind.ASK_USER: AskUserArgs,
    ActionKind.TASK_DONE: TaskDoneArgs,
    ActionKind.COMPLETE_SUBGOAL: CompleteSubgoalArgs,
    ActionKind.REVISE_PLAN: RevisePlanArgs,
    ActionKind.REGISTER_STATE: RegisterStateArgs,
    ActionKind.SAVE_NOTE: SaveNoteArgs,
    ActionKind.USE_SKILL: UseSkillArgs,
    ActionKind.REQUEST_MODE: RequestModeArgs,
}

# 需要從正規化座標換算成像素的欄位組
POINT_FIELDS: dict[ActionKind, tuple[tuple[str, str], ...]] = {
    ActionKind.CLICK: (("x", "y"),),
    ActionKind.DOUBLE_CLICK: (("x", "y"),),
    ActionKind.RIGHT_CLICK: (("x", "y"),),
    ActionKind.SCROLL: (("x", "y"),),
    ActionKind.DRAG: (("x1", "y1"), ("x2", "y2")),
}
