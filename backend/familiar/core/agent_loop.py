"""代理人迴圈（第 4 層）：截圖 → 呼叫模型 → 解析動作 → 安全檢查 → 執行，直到完成（SDD 5.1）。"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, replace
from typing import Any

from familiar.core.action_handler import ActionHandler, Handled, describe
from familiar.core.actor_step import try_actor
from familiar.executors.elements import ElementTable
from familiar.core.agent_types import LoopConfig, LoopDeps, LoopResult, StepInfo, TaskSpec
from familiar.core.events import make_event
from familiar.core.models import Action, ActionOutcome, CallError
from familiar.core.prompts import SYSTEM_INSTRUCTION, fresh_input
from familiar.core.tasks import TaskStatus
from familiar.executors.base import Screenshot
from familiar.gemini.errors import ErrorKind, classify
from familiar.gemini.interactions import AgentTurn
from familiar.gemini.model_router import NoModelAvailable, Role
from familiar.vision.images import dhash, downscale_png, thumbnail_b64, unchanged_streak

logger = logging.getLogger(__name__)

FRIENDLY_ERRORS = {
    ErrorKind.SAFETY_BLOCKED: "模型的安全機制拒絕處理目前的畫面",
    ErrorKind.BAD_REQUEST: "送給模型的請求格式有誤（詳細內容請看後端日誌）",
}
STUCK_NOTE = "最近幾步畫面幾乎沒有變化，請換一種做法，或呼叫 ask_user。"


@dataclass(frozen=True)
class Conversation:
    model: str | None = None
    interaction_id: str | None = None
    started_step: int = 0


@dataclass(frozen=True)
class Observation:
    shot: Screenshot
    model_png: bytes
    info: dict[str, str]
    elements_text: str | None
    phash: str
    thumbnail: str
    note: str | None
    table: ElementTable | None = None


class AgentLoop:
    def __init__(self, deps: LoopDeps, config: LoopConfig | None = None):
        self._deps = deps
        self._config = config or LoopConfig()

    @property
    def deps(self) -> LoopDeps:
        return self._deps

    async def run(self, spec: TaskSpec, first_index: int = 0) -> LoopResult:
        """first_index：步驟編號從哪裡開始（技能重播失敗、接手修復時接在重播步驟之後）。"""
        deps, config = self._deps, self._config
        handler = ActionHandler(deps, config, spec)
        tools = deps.backend.tools(spec.environment, spec.mode)
        started = deps.clock()
        conversation = Conversation()
        pending: tuple[ActionOutcome, ...] = ()
        history: tuple[str, ...] = ()
        hashes: tuple[str, ...] = ()
        steps = first_index
        failures = 0
        carry_calls = 0  # 第 3 層用掉、還沒記到步驟上的 Decisions 次數
        last_was_actor = False

        while True:
            stop = self._limit_reached(spec, steps, started)
            if stop:
                return stop
            observation = await self._observe(hashes)
            hashes = (*hashes, observation.phash)
            if unchanged_streak(hashes) >= config.stuck_stop_steps:
                return LoopResult(TaskStatus.FAILED, error="畫面連續多步沒有變化，任務停止", steps=steps)

            attempt = await try_actor(deps, handler, spec, observation.shot, observation.table, observation.info,
                                      observation.thumbnail, history, hashes, steps + 1, last_was_actor)
            carry_calls += attempt.decision_calls
            last_was_actor = attempt.handled is not None
            if attempt.handled is not None and attempt.action is not None:
                steps += 1
                history = (*history, attempt.line or "")
                await self._record(spec, steps - 1, attempt.model, attempt.action, attempt.handled, observation,
                                   None, attempt.duration_ms, layer="actor", decision_calls=carry_calls)
                carry_calls = 0
                conversation, pending = Conversation(), ()  # 代理人下次要從最新進度開新對話
                if attempt.handled.finish:
                    return replace(attempt.handled.finish, steps=steps)
                continue

            try:
                routed = await deps.router.call(
                    Role.ACTION,
                    lambda model: self._turn(model, spec, conversation, pending, history, observation, tools, steps),
                    est_tokens=config.est_tokens_per_turn,
                    tokens_of=lambda pair: pair[0].tokens_in,
                )
            except NoModelAvailable as error:
                if await self._wait_for_quota(spec, error):
                    return LoopResult(TaskStatus.STOPPED, steps=steps)
                continue
            except Exception as error:  # noqa: BLE001 — 依種類決定重試或結束
                failures += 1
                api_error = classify(error)
                logger.warning("呼叫代理人模型失敗（%s）：%s", api_error.kind, api_error.message)
                if api_error.kind is ErrorKind.SAFETY_BLOCKED and failures < 2:
                    conversation, pending = Conversation(), ()
                    continue
                return LoopResult(TaskStatus.FAILED, steps=steps,
                                  error=FRIENDLY_ERRORS.get(api_error.kind, "呼叫 AI 模型失敗，請稍後再試"))

            failures = 0
            turn, continued = routed.value
            conversation = Conversation(routed.model, turn.interaction_id,
                                        conversation.started_step if continued else steps)
            await deps.events.emit(make_event("quota_update", model=routed.model, quota=deps.router.snapshot()))
            if not turn.function_calls:
                return LoopResult(TaskStatus.SUCCEEDED, text=turn.text or "任務完成", steps=steps)

            outcomes: list[ActionOutcome] = []
            for position, item in enumerate(deps.backend.parse(turn.function_calls, observation.shot.frame)):
                began = time.perf_counter()
                handled = await handler.handle(item, observation.info, observation.thumbnail, steps + 1)
                steps += 1
                outcomes.append(handled.outcome)
                history = (*history, _history_line(item, handled.outcome))
                await self._record(spec, steps - 1, routed.model, item, handled, observation,
                                   turn if position == 0 else None, int((time.perf_counter() - began) * 1000),
                                   decision_calls=carry_calls if position == 0 else 0)
                carry_calls = 0 if position == 0 else carry_calls
                if handled.finish:
                    return replace(handled.finish, steps=steps)
            pending = tuple(outcomes)

    def _limit_reached(self, spec: TaskSpec, steps: int, started: Any) -> LoopResult | None:
        if self._deps.control.stopped:
            return LoopResult(TaskStatus.STOPPED, steps=steps)
        if steps >= self._config.max_steps:
            return LoopResult(TaskStatus.FAILED, error=f"超過步數上限（{self._config.max_steps} 步）", steps=steps)
        elapsed = (self._deps.clock() - started).total_seconds() / 60
        if elapsed >= self._config.max_minutes:
            return LoopResult(TaskStatus.FAILED, error=f"超過時間上限（{self._config.max_minutes:g} 分鐘）", steps=steps)
        return None

    async def _observe(self, hashes: tuple[str, ...]) -> Observation:
        executor = self._deps.executor
        shot = await executor.screenshot()
        table = await executor.elements()
        phash = dhash(shot.png)
        streak = unchanged_streak((*hashes, phash))
        return Observation(
            shot=shot,
            model_png=downscale_png(shot.png, self._config.model_screenshot_max_side),
            info=await executor.context_info(),
            elements_text=table.to_text() if table and table.elements else None,
            phash=phash,
            thumbnail=thumbnail_b64(shot.png),
            note=STUCK_NOTE if streak >= self._config.stuck_warn_steps else None,
            table=table,
        )

    async def _turn(self, model: str, spec: TaskSpec, conversation: Conversation,
                    pending: tuple[ActionOutcome, ...], history: tuple[str, ...], observation: Observation,
                    tools: list[dict[str, Any]], steps: int) -> tuple[AgentTurn, bool]:
        """回傳 (模型這一輪的回應, 是否延續原本的對話)。換模型或對話太長時開新對話（FR-112、FR-205）。"""
        fresh_needed = (
            conversation.model != model
            or not conversation.interaction_id
            or not pending
            or steps - conversation.started_step >= self._config.compress_after_steps
        )
        if fresh_needed:
            payload = fresh_input(spec.instruction, history, observation.info, observation.elements_text,
                                  observation.note, observation.model_png)
            previous = None
        else:
            context: dict[str, Any] = {**observation.info, "elements": observation.elements_text or "",
                                       **({"note": observation.note} if observation.note else {})}
            last = replace(pending[-1], data={**pending[-1].data, **context}, screenshot_png=observation.model_png)
            payload = self._deps.backend.build_results((*pending[:-1], last))
            previous = conversation.interaction_id
        turn = await self._deps.client.create_turn(model=model, system_instruction=SYSTEM_INSTRUCTION,
                                                   input=payload, tools=tools, previous_interaction_id=previous)
        return turn, not fresh_needed

    async def _wait_for_quota(self, spec: TaskSpec, error: NoModelAvailable) -> bool:
        """所有模型都暫時不能用：轉為 waiting_quota，等到恢復或被停止。回傳 True 表示被停止。"""
        deps = self._deps
        await deps.on_status(TaskStatus.WAITING_QUOTA)
        ready_at = error.ready_at.isoformat() if error.ready_at else None
        await deps.events.emit(make_event("waiting_quota", task_id=spec.task_id, ready_at=ready_at))
        seconds = (error.ready_at - deps.clock()).total_seconds() if error.ready_at else 300
        stopped = await deps.control.sleep_or_stop(min(max(seconds, 1), self._config.max_quota_wait_sec))
        if not stopped:
            await deps.on_status(TaskStatus.RUNNING)
        return stopped

    async def _record(self, spec: TaskSpec, index: int, model: str, item: Action | CallError,
                      handled: Handled, observation: Observation, turn: AgentTurn | None,
                      duration_ms: int, layer: str = "agent", decision_calls: int = 0) -> None:
        """turn 只在該輪第一個動作傳入，token 用量記在那一步。"""
        outcome = handled.outcome
        action = describe(item) if isinstance(item, Action) else {"kind": item.name, "args": {}, "intent": ""}
        safety = ({"decision": item.safety.decision, "explanation": item.safety.explanation}
                  if isinstance(item, Action) and item.safety else None)
        info = StepInfo(index=index, layer=layer, model=model, action=action, ok=outcome.ok,
                        message=outcome.message, safety=safety, screenshot_png=observation.shot.png,
                        phash=observation.phash, duration_ms=duration_ms,
                        tokens_in=turn.tokens_in if turn else 0, tokens_out=turn.tokens_out if turn else 0,
                        locator=handled.locator, page_url=observation.info.get("url"),
                        decision_calls=decision_calls)
        await self._deps.on_step(info)
        await self._deps.events.emit(make_event(
            "step", task_id=spec.task_id, index=index, layer=layer, model=model, action=action,
            ok=outcome.ok, message=outcome.message, thumbnail=observation.thumbnail))


def _history_line(item: Action | CallError, outcome: ActionOutcome) -> str:
    label = (item.intent or item.kind.value) if isinstance(item, Action) else item.name
    return f"{label} → {'成功' if outcome.ok else '失敗：' + outcome.message}"
