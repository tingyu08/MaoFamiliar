"""在呼叫代理人模型之前，先試第 3 層（Decisions 選候選）。失敗或不適用時安靜地交回代理人。"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Sequence

from familiar.core.action_handler import ActionHandler, Handled
from familiar.core.agent_types import LoopDeps, TaskSpec
from familiar.core.models import Action, ActionKind, Risk
from familiar.executors.elements import ElementTable
from familiar.executors.base import Screenshot
from familiar.vision.images import unchanged_streak

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ActorAttempt:
    decision_calls: int = 0
    handled: Handled | None = None
    action: Action | None = None
    model: str | None = None
    line: str | None = None
    duration_ms: int = 0


NOT_TRIED = ActorAttempt()


async def try_actor(deps: LoopDeps, handler: ActionHandler, spec: TaskSpec, shot: Screenshot,
                    table: ElementTable | None, info: dict[str, str], thumbnail: str,
                    history: Sequence[str], hashes: Sequence[str], next_index: int,
                    last_was_actor: bool = False) -> ActorAttempt:
    actor = deps.actor
    if actor is None or not actor.config.enabled or table is None:
        return NOT_TRIED
    if last_was_actor and unchanged_streak(tuple(hashes)) >= actor.config.stall_steps:
        # 上一步是第 3 層點的，畫面卻沒變：可能點錯了，交給代理人換方法（FR-655）。
        # 只看第 3 層自己的上一步：輸入文字這類小變化，差異雜湊常常偵測不到。
        return NOT_TRIED
    began = time.perf_counter()
    try:
        decision = await actor.choose(shot.png, table, spec.instruction, history)
    except Exception as error:  # noqa: BLE001 — 第 3 層失敗不影響任務，交給代理人
        logger.info("第 3 層選候選失敗，改由代理人處理：%s", error)
        return NOT_TRIED
    if decision.element is None:
        return ActorAttempt(decision_calls=decision.decision_calls)
    element = decision.element
    action = Action(ActionKind.CLICK_ELEMENT, {"ref": element.ref}, intent=f"點擊「{element.name}」",
                    risk=Risk.HIGH if decision.risky else Risk.LOW)
    handled = await handler.handle(action, info, thumbnail, next_index)
    line = f"點擊「{element.name}」（第 3 層，信心 {decision.confidence:.2f}）→ " + (
        "成功" if handled.outcome.ok else "失敗：" + handled.outcome.message)
    return ActorAttempt(decision.decision_calls, handled, action, decision.model, line,
                        int((time.perf_counter() - began) * 1000))
