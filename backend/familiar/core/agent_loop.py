"""代理人迴圈（第 4 層）：截圖 → 呼叫模型 → 解析動作 → 安全檢查 → 執行，直到完成（SDD 5.1）。"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, replace
from typing import Any

from familiar.core.action_handler import ActionHandler, Handled, describe
from familiar.core.actor_step import try_actor
from familiar.executors.elements import ElementTable
from familiar.core.agent_types import LoopConfig, LoopDeps, LoopResult, StepInfo, TaskSpec
from familiar.core.events import make_event
from familiar.core.models import Action, ActionKind, ActionOutcome, CallError, OperatingMode, thaw
from familiar.core.prompts import fresh_input, system_instruction_for
from familiar.core.reflex_step import ReflexTick, TickSink, run_reflex
from familiar.core.tasks import TaskStatus
from familiar.executors.base import Screenshot
from familiar.gemini.errors import ErrorKind, classify
from familiar.gemini.interactions import AgentTurn
from familiar.gemini.model_router import NoModelAvailable, Role
from familiar.vision.images import crop_png, dhash, downscale_png, thumbnail_b64, unchanged_streak

logger = logging.getLogger(__name__)

FRIENDLY_ERRORS = {
    ErrorKind.SAFETY_BLOCKED: "模型的安全機制拒絕處理目前的畫面",
    ErrorKind.BAD_REQUEST: "送給模型的請求格式有誤（詳細內容請看後端日誌）",
}
STUCK_NOTE = "最近幾步畫面幾乎沒有變化，請換一種做法，或呼叫 ask_user。"
REPEAT_NOTE = ("你已經連續 {count} 次做一模一樣的動作（{label}）。如果沒有進展，請仔細看畫面上的提示"
               "（文字、箭頭、按鍵圖示，例如「↑ JUMP」表示要按上鍵跳），換一種做法。")


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
        reflex_steps = 0  # 反射層按鍵的步數不算進 max_steps（那是給代理人與第 3 層的上限）
        recent: tuple[tuple[str, str], ...] = ()  # 代理人最近的動作（簽章, 說明），偵測一直重複同一個動作
        looked_only = False  # 上一輪代理人只呼叫了 zoom

        while True:
            stop = self._limit_reached(spec, steps - reflex_steps, started)
            if stop:
                return stop
            observation = await self._observe(hashes, realtime=spec.mode is OperatingMode.REALTIME)
            if not looked_only:  # 上一輪只有 zoom（只看不動）時畫面本來就不會變，不算卡住
                hashes = (*hashes, observation.phash)
            looked_only = False
            if unchanged_streak(hashes) >= config.stuck_stop_steps:
                return LoopResult(TaskStatus.FAILED, error="畫面連續多步沒有變化，任務停止", steps=steps)
            observation = _with_repeat_note(observation, recent, config.repeat_warn_actions)

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
            if not turn.function_calls:  # 模型直接用文字回答，視同回報完成，也要經過完成檢查
                summary = turn.text or "任務完成"
                finish, rejection, calls = await handler.judge_completion(summary, steps)
                if finish is not None:
                    return replace(finish, steps=steps)
                carry_calls += calls
                history = (*history, f"回報完成「{summary[:80]}」→ {rejection}")
                conversation, pending = Conversation(), ()  # 開新對話，讓模型從歷史看到被退回的原因
                continue

            outcomes: list[ActionOutcome] = []
            items = deps.backend.parse(turn.function_calls, observation.shot.frame)
            looked_only = bool(items) and all(_is_zoom(item) for item in items)
            for position, item in enumerate(items):
                began = time.perf_counter()
                if _is_reflex(item) and deps.reflex is not None and deps.reflex.config.enabled:
                    run = await run_reflex(deps, handler, item, observation.info, steps,
                                           self._tick_recorder(spec, observation))
                    steps += run.ticks
                    reflex_steps += run.ticks
                    handled = run.handled
                else:
                    handled = await handler.handle(item, observation.info, observation.thumbnail, steps + 1)
                steps += 1
                recent = (*recent, _signature(item))[-20:]
                outcomes.append(handled.outcome)
                history = (*history, _history_line(item, handled.outcome))
                await self._record(spec, steps - 1, routed.model, item, handled, observation,
                                   turn if position == 0 else None, int((time.perf_counter() - began) * 1000),
                                   decision_calls=handled.decision_calls + (carry_calls if position == 0 else 0))
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

    async def _observe(self, hashes: tuple[str, ...], realtime: bool = False) -> Observation:
        executor = self._deps.executor
        shot = await executor.screenshot()
        table = await executor.elements()
        # 遊戲模式：卡住偵測只看遊戲畫面那一塊；整頁的雜湊會被不動的網站框架與廣告主導
        focus = await executor.focus_box() if realtime else None
        phash = dhash(crop_png(shot.png, focus) if focus else shot.png)
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
        turn = await self._deps.client.create_turn(model=model,
                                                   system_instruction=system_instruction_for(
                                                       spec.mode is OperatingMode.REALTIME),
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

    def _tick_recorder(self, spec: TaskSpec, observation: Observation) -> TickSink:
        async def record(tick: ReflexTick) -> None:
            seen = replace(observation, shot=tick.shot, phash=tick.phash, thumbnail=tick.thumbnail, note=None)
            await self._record(spec, tick.index, tick.model, tick.action, tick.handled, seen, None,
                               tick.duration_ms, layer="reflex", decision_calls=1)
        return record

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


def _signature(item: Action | CallError) -> tuple[str, str]:
    """(比對用的簽章, 給模型看的說明)：種類＋參數相同就算同一個動作。"""
    if isinstance(item, CallError):
        return f"error:{item.name}", item.name
    args = json.dumps(thaw(item.args), sort_keys=True, ensure_ascii=False)
    return f"{item.kind.value}:{args}", f"{item.kind.value} {args}"


def trailing_repeats(recent: tuple[tuple[str, str], ...]) -> int:
    """最後一個動作連續出現幾次。"""
    if not recent:
        return 0
    last = recent[-1][0]
    count = 0
    for signature, _ in reversed(recent):
        if signature != last:
            break
        count += 1
    return count


def _with_repeat_note(observation: Observation, recent: tuple[tuple[str, str], ...], warn_at: int) -> Observation:
    count = trailing_repeats(recent)
    if count < warn_at:
        return observation
    note = REPEAT_NOTE.format(count=count, label=recent[-1][1])
    return replace(observation, note=f"{observation.note}\n{note}" if observation.note else note)


def _is_reflex(item: Action | CallError) -> bool:
    return isinstance(item, Action) and item.kind is ActionKind.SET_REFLEX


def _is_zoom(item: Action | CallError) -> bool:
    return isinstance(item, Action) and item.kind is ActionKind.ZOOM


def _history_line(item: Action | CallError, outcome: ActionOutcome) -> str:
    label = (item.intent or item.kind.value) if isinstance(item, Action) else item.name
    if outcome.ok and _is_reflex(item):  # 反射層的統計要留在歷史裡，開新對話時代理人才看得到
        return f"{label} → {outcome.message[:300]}"
    return f"{label} → {'成功' if outcome.ok else '失敗：' + outcome.message}"
