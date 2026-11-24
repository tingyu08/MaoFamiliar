"""第 1 層：重播技能（SDD 5.3）。不呼叫任何 AI。

元素定位順序（SRS FR-403）：role＋名稱 → 名稱 → CSS → 錄製時的座標。
每一步仍經過安全規則與高風險確認（重用 ActionHandler）。
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from familiar.core.action_handler import ActionHandler, describe
from familiar.core.agent_types import LoopConfig, LoopDeps, StepInfo, TaskSpec
from familiar.core.events import make_event
from familiar.core.models import Action, ActionKind, Frame, Risk
from familiar.executors.elements import Element, ElementTable
from familiar.knowledge.skills import SkillStep, SkillVersion
from familiar.vision.images import dhash, thumbnail_b64

RETRY_DELAY_SEC = 1.0
NAV_CHECK_SEC = 3.0
POINT_KINDS = frozenset({"click", "double_click", "right_click"})


@dataclass(frozen=True)
class PlayResult:
    ok: bool
    completed: int              # 成功重播的步數
    failed_step: int | None = None
    message: str = ""
    stopped: bool = False


def _path(url: str | None) -> str:
    parsed = urlparse(url or "")
    return f"{parsed.netloc}{parsed.path}"


def _element_action(step: SkillStep, element: Element, risk: Risk) -> Action:
    if step.kind in ("fill_element",):
        return Action(ActionKind.FILL_ELEMENT, {"ref": element.ref, "text": step.args.get("text", "")},
                      intent=step.intent, risk=risk)
    if step.kind in ("click", "click_element"):
        return Action(ActionKind.CLICK_ELEMENT, {"ref": element.ref}, intent=step.intent, risk=risk)
    x, y = element.center
    return Action(ActionKind(step.kind), {"x": x, "y": y}, intent=step.intent, risk=risk)


def _point_action(step: SkillStep, frame: Frame, risk: Risk) -> Action | None:
    locator = step.locator
    if step.kind not in POINT_KINDS and step.kind != "click_element":
        return None  # 填寫欄位只靠座標太危險，交給自我修復
    if not locator or not locator.point:
        return None
    width, height = locator.frame or (frame.width, frame.height)
    x = round(locator.point[0] * frame.width / width)
    y = round(locator.point[1] * frame.height / height)
    kind = ActionKind.CLICK if step.kind == "click_element" else ActionKind(step.kind)
    return Action(kind, {"x": x, "y": y}, intent=step.intent, risk=risk)


class SkillPlayer:
    def __init__(self, deps: LoopDeps, spec: TaskSpec, first_index: int = 0):
        self._deps = deps
        self._spec = spec
        self._index = first_index
        self._handler = ActionHandler(deps, LoopConfig(), spec)

    @property
    def next_index(self) -> int:
        return self._index

    async def play(self, version: SkillVersion) -> PlayResult:
        for position, step in enumerate(version.steps):
            if self._deps.control.stopped:
                return PlayResult(False, position, position, "任務已停止", stopped=True)
            ok, message = await self._play_step(step)
            if not ok:
                await asyncio.sleep(RETRY_DELAY_SEC)  # 頁面可能還在載入：重試一次
                ok, message = await self._play_step(step)
            if self._deps.control.stopped:
                return PlayResult(False, position, position, "任務已停止", stopped=True)
            if not ok:
                return PlayResult(False, position, position, message)
        return PlayResult(True, len(version.steps))

    async def _resolve(self, step: SkillStep, frame: Frame, table: ElementTable | None) -> tuple[Action | None, str]:
        risk = Risk.HIGH if step.risk == "high" else Risk.LOW
        if step.kind not in POINT_KINDS | {"click_element", "fill_element"}:
            return Action(ActionKind(step.kind), dict(step.args), intent=step.intent, risk=risk), "直接執行"
        locator = step.locator
        if locator and table:
            for strategy, element in (("role＋名稱", table.match(locator.role, locator.name)),
                                      ("名稱", table.match_name(locator.name))):
                if element:
                    return _element_action(step, element, risk), strategy
        if locator and locator.css:
            ref = await self._deps.executor.ref_for_css(locator.css)
            if ref:
                css_element = Element(ref, locator.role, locator.name, 0, 0, 0, 0)
                if step.kind in ("click", "click_element", "fill_element"):
                    return _element_action(step, css_element, risk), "CSS"
        action = _point_action(step, frame, risk)
        if action is None:
            return None, "找不到元素"
        # 座標上必須還是同一種元素（例如按鈕），否則網頁版面已經改了，不能盲點
        found = await self._deps.executor.element_at(action.args["x"], action.args["y"])
        if not found or not locator or found.get("role") != locator.role:
            return None, "座標上的元素已經不同"
        return action, "座標"

    async def _play_step(self, step: SkillStep) -> tuple[bool, str]:
        executor = self._deps.executor
        began = time.perf_counter()
        shot = await executor.screenshot()
        info = await executor.context_info()
        table = await executor.elements()
        action, strategy = await self._resolve(step, shot.frame, table)
        thumbnail = thumbnail_b64(shot.png)
        if action is None:
            message = f"找不到「{step.locator.name if step.locator else step.intent}」"
            placeholder = Action(ActionKind(step.kind), dict(step.args), intent=step.intent)
            await self._record(placeholder, False, message, shot.png, info, None, thumbnail,
                               int((time.perf_counter() - began) * 1000))
            return False, message
        handled = await self._handler.handle(action, info, thumbnail, self._index + 1)
        ok, message = handled.outcome.ok, handled.outcome.message
        if ok and step.url_after and _path(step.url_after) != _path(step.page_url):
            ok, message = await self._wait_for_url(step.url_after)
        await self._record(action, ok, message or f"定位方式：{strategy}", shot.png, info, handled.locator,
                           thumbnail, int((time.perf_counter() - began) * 1000))
        if handled.finish:  # 使用者在確認時停止了任務
            self._deps.control.request_stop()
        return ok, message

    async def _wait_for_url(self, expected: str) -> tuple[bool, str]:
        deadline = time.perf_counter() + NAV_CHECK_SEC
        while time.perf_counter() < deadline:
            current = (await self._deps.executor.context_info()).get("url")
            if _path(current) == _path(expected):
                return True, ""
            await asyncio.sleep(0.2)
        return False, "畫面沒有前往預期的頁面"

    async def _record(self, action: Action, ok: bool, message: str, png: bytes, info: dict[str, str],
                      locator: dict[str, Any] | None, thumbnail: str, duration_ms: int) -> None:
        index = self._index
        self._index += 1
        described = describe(action)
        await self._deps.on_step(StepInfo(index=index, layer="skill", model=None, action=described, ok=ok,
                                          message=message, safety=None, screenshot_png=png, phash=dhash(png),
                                          duration_ms=duration_ms, locator=locator, page_url=info.get("url")))
        await self._deps.events.emit(make_event("step", task_id=self._spec.task_id, index=index, layer="skill",
                                                model=None, action=described, ok=ok, message=message,
                                                thumbnail=thumbnail))
