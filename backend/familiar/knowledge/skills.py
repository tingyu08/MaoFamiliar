"""技能：錄製下來、可以重播的動作序列（SDD 3.1、6.2）。全部是不可變的 pydantic 模型。"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Literal, Sequence
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field

from familiar.core.tasks import TaskRecord, TaskStatus
from familiar.storage.task_repository import StepRecord

SCHEMA_VERSION = 1
MAX_RELEARN_FAILURES = 3  # 連續失敗幾次就標記「需要重新學習」（FR-406）

# 可以重播的動作（流程、規劃、知識類動作不錄）
REPLAYABLE_KINDS = frozenset({
    "click", "double_click", "right_click", "drag", "scroll", "type_text", "press_keys",
    "navigate", "go_back", "click_element", "fill_element", "wait",
})
ELEMENT_STEP_KINDS = frozenset({"click", "double_click", "right_click", "click_element", "fill_element"})


class SkillBuildError(ValueError):
    """這個任務不能存成技能（原因寫在訊息裡，會顯示給使用者）。"""


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")


class Locator(_Frozen):
    role: str = ""
    name: str = ""
    css: str = ""
    point: tuple[float, float] | None = None
    frame: tuple[int, int] | None = None


class SkillStep(_Frozen):
    kind: str
    args: dict[str, Any] = Field(default_factory=dict)
    intent: str = ""
    risk: Literal["low", "high"] = "low"
    locator: Locator | None = None
    page_url: str | None = None
    url_after: str | None = None


class SkillVersion(_Frozen):
    schema_version: Literal[1] = SCHEMA_VERSION
    skill_id: str
    version: int = Field(ge=1)
    start_url: str
    steps: tuple[SkillStep, ...] = Field(min_length=1)
    source_task_id: str | None = None
    created_at: datetime


class SkillMeta(_Frozen):
    skill_id: str
    name: str = Field(min_length=1, max_length=60)
    target_id: str
    start_url: str
    instruction: str
    current_version: int = 1
    success_count: int = 0
    fail_count: int = 0
    consecutive_failures: int = 0
    created_at: datetime
    updated_at: datetime

    @property
    def needs_relearn(self) -> bool:
        return self.consecutive_failures >= MAX_RELEARN_FAILURES


def target_id_for(url: str) -> str:
    """瀏覽器目標以網域（含連接埠）識別，例如 web_example.com。"""
    netloc = urlparse(url).netloc.lower() or "unknown"
    return "web_" + re.sub(r"[^a-z0-9._-]", "_", netloc)


def _step_from_record(record: StepRecord, url_after: str | None) -> SkillStep:
    action = record.action
    args = {k: v for k, v in (action.get("args") or {}).items() if k != "ref"}  # 元素編號每次都不同，不錄
    locator = Locator.model_validate(record.locator) if record.locator else None
    return SkillStep(kind=action["kind"], args=args, intent=action.get("intent", ""),
                     risk="high" if action.get("risk") == "high" else "low", locator=locator,
                     page_url=record.page_url, url_after=url_after)


def build_steps(task: TaskRecord, records: Sequence[StepRecord]) -> tuple[SkillStep, ...]:
    """把成功任務的步驟轉成技能步驟（SRS FR-401、FR-402）。"""
    if task.status is not TaskStatus.SUCCEEDED:
        raise SkillBuildError("只有成功完成的任務才能存成技能")
    return steps_from_records(records)


def steps_from_records(records: Sequence[StepRecord]) -> tuple[SkillStep, ...]:
    if any(r.action.get("kind") == "ask_user" for r in records):
        raise SkillBuildError("這個任務中途請你協助過（例如登入），不能存成自動重播的技能")
    usable = [r for r in records if r.result.get("ok") and r.action.get("kind") in REPLAYABLE_KINDS]
    if not usable:
        raise SkillBuildError("這個任務沒有可以重播的操作步驟")
    if any(r.action["kind"] in ELEMENT_STEP_KINDS and not r.locator for r in usable):
        raise SkillBuildError("有步驟缺少元素定位資訊（可能是舊版本記錄的任務），請重新執行一次再存")
    urls = [r.page_url for r in usable[1:]] + [None]
    return tuple(_step_from_record(r, after) for r, after in zip(usable, urls))
