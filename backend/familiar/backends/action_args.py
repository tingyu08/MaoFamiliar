"""每種動作的參數 schema（系統邊界驗證：模型輸出一律視為不可信）。"""

from __future__ import annotations

from typing import Annotated, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

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


MIN_ZOOM_SPAN = 20  # 正規化座標：放大區域的寬、高至少是畫面的 2%


class ZoomArgs(_Base):
    x1: Coord
    y1: Coord
    x2: Coord
    y2: Coord

    @model_validator(mode="after")
    def _valid_region(self) -> "ZoomArgs":
        if self.x2 - self.x1 < MIN_ZOOM_SPAN or self.y2 - self.y1 < MIN_ZOOM_SPAN:
            raise ValueError(f"區域要從左上 (x1, y1) 到右下 (x2, y2)，寬和高都至少 {MIN_ZOOM_SPAN}")
        return self


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


REFLEX_UNKNOWN = "unknown"  # 保留給「沒有規則符合」
MAX_REFLEX_SECONDS = 2.0
MAX_REFLEX_ITERATIONS = 60


class ReflexRuleArgs(BaseModel):
    """反射規則：畫面符合 when 的描述時，立刻做 action。"""

    model_config = ConfigDict(frozen=True, extra="ignore")
    id: str = Field(pattern=r"^[a-z0-9_]{1,30}$")
    when: str = Field(min_length=1, max_length=200)
    action: Literal["hold", "tap", "wait"]
    keys: list[Key] = Field(default_factory=list, max_length=3)
    seconds: float = Field(default=0.3, gt=0, le=MAX_REFLEX_SECONDS)
    times: int = Field(default=1, ge=1, le=5)

    @model_validator(mode="after")
    def _keys_for_key_actions(self) -> "ReflexRuleArgs":
        if self.id == REFLEX_UNKNOWN:
            raise ValueError(f"規則代號不能用保留字 {REFLEX_UNKNOWN}")
        if self.action != "wait" and not self.keys:
            raise ValueError(f"規則 {self.id}：{self.action} 需要 keys")
        return self


class SetReflexArgs(_Base):
    question: str = Field(min_length=1, max_length=500)
    rules: list[ReflexRuleArgs] = Field(min_length=2, max_length=12)
    iterations: int = Field(default=20, ge=1, le=MAX_REFLEX_ITERATIONS)

    @field_validator("rules")
    @classmethod
    def _unique_ids(cls, rules: list[ReflexRuleArgs]) -> list[ReflexRuleArgs]:
        ids = [rule.id for rule in rules]
        if len(set(ids)) != len(ids):
            raise ValueError("規則代號不能重複")
        return rules


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
    ActionKind.SET_REFLEX: SetReflexArgs,
    ActionKind.NAVIGATE: NavigateArgs,
    ActionKind.GO_BACK: GoBackArgs,
    ActionKind.CLICK_ELEMENT: ClickElementArgs,
    ActionKind.FILL_ELEMENT: FillElementArgs,
    ActionKind.ZOOM: ZoomArgs,
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
    ActionKind.ZOOM: (("x1", "y1"), ("x2", "y2")),
}
