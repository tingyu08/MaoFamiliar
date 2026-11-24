"""重播一個技能；失敗時從失敗那一步交給代理人接手（自我修復），成功就存成技能的新版本（SRS FR-404）。"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from familiar.core.agent_loop import AgentLoop
from familiar.core.agent_types import LoopResult, TaskSpec
from familiar.core.events import make_event
from familiar.core.tasks import TaskStatus
from familiar.knowledge.skill_player import SkillPlayer
from familiar.knowledge.skill_store import SkillStore
from familiar.knowledge.skills import SkillBuildError, SkillVersion, steps_from_records
from familiar.storage.task_repository import TaskRepository

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ReplayContext:
    skills: SkillStore
    repo: TaskRepository
    clock: Callable[[], datetime]


def heal_instruction(instruction: str, version: SkillVersion, failed_step: int, reason: str) -> str:
    done = [s.intent or s.kind for s in version.steps[:failed_step]]
    remaining = [s.intent or s.kind for s in version.steps[failed_step:]]
    parts = [f"原本的任務：{instruction}",
             "這個任務先用錄好的步驟自動重播，但在第 {} 步失敗了（{}）。".format(failed_step + 1, reason)]
    if done:
        parts.append("已經完成的步驟：" + "；".join(done))
    parts.append("原本接下來要做的步驟（網頁可能改版，請依目前畫面判斷）：" + "；".join(remaining))
    parts.append("請從目前的畫面接著完成原本的任務。")
    return "\n".join(parts)


async def _save_healed(ctx: ReplayContext, spec: TaskSpec, version: SkillVersion, failed_step: int,
                       first_agent_index: int) -> str:
    records = [r for r in ctx.repo.steps(spec.task_id) if r.idx >= first_agent_index]
    try:
        healed = steps_from_records(records)
    except SkillBuildError as error:
        logger.info("自我修復成功但不存新版本：%s", error)
        return "（這次修復的步驟不適合存成技能，技能維持原版本）"
    meta = ctx.skills.add_version(version.skill_id, (*version.steps[:failed_step], *healed), spec.task_id,
                                  ctx.clock())
    return f"（已自動修復技能，存成第 {meta.current_version} 版）"


async def run_replay(ctx: ReplayContext, loop: AgentLoop, spec: TaskSpec, version: SkillVersion,
                     original_instruction: str) -> LoopResult:
    player = SkillPlayer(loop.deps, spec)
    played = await player.play(version)
    if played.ok:
        ctx.skills.record_result(version.skill_id, True, ctx.clock())
        return LoopResult(TaskStatus.SUCCEEDED, text=f"技能重播完成（{played.completed} 步，沒有呼叫 AI）",
                          steps=player.next_index)
    if played.stopped:
        return LoopResult(TaskStatus.STOPPED, steps=player.next_index)

    failed = played.failed_step or 0
    await loop.deps.events.emit(make_event("skill_heal", task_id=spec.task_id, failed_step=failed,
                                           reason=played.message))
    heal_spec = TaskSpec(task_id=spec.task_id, environment=spec.environment, mode=spec.mode,
                         instruction=heal_instruction(original_instruction, version, failed, played.message))
    first_agent_index = player.next_index
    result = await loop.run(heal_spec, first_index=first_agent_index)
    if result.status is not TaskStatus.SUCCEEDED:
        ctx.skills.record_result(version.skill_id, False, ctx.clock())
        return result
    note = await _save_healed(ctx, spec, version, failed, first_agent_index)
    ctx.skills.record_result(version.skill_id, True, ctx.clock())
    return LoopResult(TaskStatus.SUCCEEDED, text=f"{result.text or '任務完成'}{note}", steps=result.steps)
