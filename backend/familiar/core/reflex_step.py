"""執行代理人用 set_reflex 交代的反射規則：截遊戲畫面 → Decisions 選規則 → 立刻按鍵，重複數輪。

每輪只花一次 Decisions（約 1 秒），不呼叫代理人模型；跑完把統計回報給代理人，由它決定下一步。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, replace
from typing import Awaitable, Callable

from familiar.backends.action_args import REFLEX_UNKNOWN
from familiar.core.action_handler import ActionHandler, Handled
from familiar.core.agent_types import LoopDeps, LoopResult
from familiar.core.models import Action, ActionOutcome
from familiar.core.tasks import TaskStatus
from familiar.executors.base import Screenshot
from familiar.gemini.model_router import NoModelAvailable
from familiar.knowledge.reflex import ReflexPolicy, game_image, policy_from_args, rule_action
from familiar.vision.images import dhash, thumbnail_b64, unchanged_streak

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ReflexTick:
    """反射層實際按下按鍵的一輪，會記成一個步驟（layer = reflex）。"""

    index: int
    action: Action
    handled: Handled
    model: str
    shot: Screenshot
    phash: str
    thumbnail: str
    duration_ms: int


@dataclass(frozen=True)
class ReflexRun:
    handled: Handled   # set_reflex 本身的結果（回報給代理人的統計）
    ticks: int         # 記成步驟的輪數


TickSink = Callable[[ReflexTick], Awaitable[None]]


@dataclass(frozen=True)
class _Progress:
    chosen: tuple[str, ...] = ()
    latencies_ms: tuple[int, ...] = ()
    ticks: int = 0
    unknown_streak: int = 0
    hashes: tuple[str, ...] = ()


def _summary(policy: ReflexPolicy, progress: _Progress, reason: str) -> str:
    counts = {rule_id: progress.chosen.count(rule_id) for rule_id in (*[r.id for r in policy.rules], REFLEX_UNKNOWN)}
    used = "、".join(f"{rule_id} {n} 次" for rule_id, n in counts.items() if n) or "（沒有）"
    average = sum(progress.latencies_ms) // len(progress.latencies_ms) if progress.latencies_ms else 0
    last = progress.chosen[-1] if progress.chosen else "（沒有）"
    return (f"反射層跑了 {len(progress.chosen)} 輪（按鍵 {progress.ticks} 次，Decisions 平均 {average} ms），"
            f"停止原因：{reason}。各規則次數：{used}。最後一輪：{last}。"
            "請看最新截圖決定下一步：可以調整規則再呼叫 set_reflex，或改用其他動作。")


async def run_reflex(deps: LoopDeps, handler: ActionHandler, action: Action, info: dict[str, str],
                     first_index: int, on_tick: TickSink) -> ReflexRun:
    decider = deps.reflex
    assert decider is not None  # 呼叫端已確認反射層有啟用
    config = decider.config
    policy = policy_from_args(action.args, config.max_iterations)
    progress = _Progress()
    reason = f"跑完 {policy.iterations} 輪"
    await deps.executor.reveal_focus()  # 遊戲畫面被視窗切掉時先捲到完整可見，Decisions 才看得到全貌
    for _ in range(policy.iterations):
        if deps.control.stopped:
            return _finish(action, policy, progress, "任務已停止", LoopResult(TaskStatus.STOPPED))
        began = time.perf_counter()
        shot = await deps.executor.screenshot()
        image = game_image(shot.png, await deps.executor.focus_box(), config.max_side)
        phash = dhash(image)
        progress = _with(progress, hashes=(*progress.hashes, phash)[-config.stall_ticks:])
        if len(progress.hashes) >= config.stall_ticks and unchanged_streak(progress.hashes) >= config.stall_ticks:
            reason = f"遊戲畫面連續 {config.stall_ticks} 輪沒有變化"
            break
        try:
            choice = await decider.choose(image, policy)
        except NoModelAvailable:
            reason = "Decisions 額度暫時用完"
            break
        except Exception as error:  # noqa: BLE001 — 反射層出錯就交回代理人，不讓任務失敗
            logger.warning("反射層呼叫 Decisions 失敗：%s", error)
            reason = "Decisions 呼叫失敗"
            break
        latency = int((time.perf_counter() - began) * 1000)
        progress = _with(progress, latencies_ms=(*progress.latencies_ms, latency))
        if choice.rule is None:
            progress = _with(progress, chosen=(*progress.chosen, REFLEX_UNKNOWN),
                             unknown_streak=progress.unknown_streak + 1)
            if progress.unknown_streak >= config.max_unknown:
                reason = f"連續 {config.max_unknown} 輪沒有規則符合畫面"
                break
            continue
        key_action = rule_action(choice.rule)
        thumbnail = thumbnail_b64(shot.png)
        handled = await handler.handle(key_action, info, thumbnail, first_index + progress.ticks + 1)
        await on_tick(ReflexTick(first_index + progress.ticks, key_action, handled, choice.model, shot, phash,
                                 thumbnail, int((time.perf_counter() - began) * 1000)))
        progress = _with(progress, chosen=(*progress.chosen, choice.rule.id), ticks=progress.ticks + 1,
                         unknown_streak=0)
        if handled.finish:
            return _finish(action, policy, progress, "任務已停止", handled.finish)
        if not handled.outcome.ok:
            reason = f"按鍵失敗：{handled.outcome.message}"
            break
    return _finish(action, policy, progress, reason, None)


def _with(progress: _Progress, **changes: object) -> _Progress:
    return replace(progress, **changes)  # type: ignore[arg-type]


def _finish(action: Action, policy: ReflexPolicy, progress: _Progress, reason: str,
            finish: LoopResult | None) -> ReflexRun:
    # 按鍵的輪次各自記了 1 次 Decisions；沒按鍵的輪次（unknown）算在 set_reflex 這一步
    unrecorded = len(progress.latencies_ms) - progress.ticks
    outcome = ActionOutcome(action.call_id, action.kind.value, ok=True,
                            message=_summary(policy, progress, reason))
    return ReflexRun(Handled(outcome, finish, decision_calls=max(unrecorded, 0)), progress.ticks)
