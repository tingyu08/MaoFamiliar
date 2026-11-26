"""處理模型回傳的單一函式呼叫：流程動作、安全檢查、使用者確認、交給執行器。"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from familiar.core.agent_types import LoopConfig, LoopDeps, LoopResult, TaskSpec
from familiar.core.events import make_event
from familiar.core.models import Action, ActionKind, ActionOutcome, CallError, Environment, thaw
from familiar.core.safety import SafetyContext, SafetyResult, Verdict, evaluate
from familiar.core.tasks import TaskStatus
from familiar.vision.images import zoom_png

logger = logging.getLogger(__name__)

PLANNING_KINDS = frozenset({ActionKind.COMPLETE_SUBGOAL, ActionKind.REVISE_PLAN})
KNOWLEDGE_KINDS = frozenset({ActionKind.REGISTER_STATE, ActionKind.SAVE_NOTE, ActionKind.USE_SKILL,
                             ActionKind.REQUEST_MODE})
PASSWORD_MESSAGE = "這是密碼欄位，AI 不可以輸入。請呼叫 ask_user 請使用者自己輸入。"
ZOOM_MESSAGE = "第一張圖是放大後的區域；如果後面還有一張，那是目前的整個畫面。"


@dataclass(frozen=True)
class Handled:
    outcome: ActionOutcome
    finish: LoopResult | None = None
    locator: dict[str, Any] | None = None
    decision_calls: int = 0


def describe(action: Action) -> dict[str, Any]:
    return {"kind": action.kind.value, "args": thaw(action.args), "intent": action.intent,
            "risk": action.risk.value}


class ActionHandler:
    def __init__(self, deps: LoopDeps, config: LoopConfig, spec: TaskSpec):
        self._deps = deps
        self._config = config
        self._spec = spec
        self._rejections = 0  # 完成檢查退回的次數（每個任務各自計算）

    async def handle(self, item: Action | CallError, info: dict[str, str], thumbnail: str, steps: int) -> Handled:
        if isinstance(item, CallError):
            return Handled(ActionOutcome(item.call_id, item.name, ok=False, message=item.message))
        action = item
        if action.kind is ActionKind.TASK_DONE:
            summary = str(action.args.get("summary", ""))
            finish, rejection, calls = await self.judge_completion(summary, steps)
            if finish is None:
                return Handled(self._error(action, rejection or ""), decision_calls=calls)
            outcome = self._ok(action) if finish.status is TaskStatus.SUCCEEDED else self._error(action, finish.error or "")
            return Handled(outcome, finish, decision_calls=calls)
        if action.kind is ActionKind.ZOOM:
            return await self._zoom(action)
        if action.kind is ActionKind.ASK_USER:
            return await self._ask_user(action, steps)
        if action.kind in PLANNING_KINDS:
            await self._deps.events.emit(make_event("plan_updated", task_id=self._spec.task_id, **describe(action)))
            return Handled(self._ok(action))
        if action.kind is ActionKind.SET_REFLEX:  # 有啟用時由代理人迴圈處理，走到這裡表示停用
            return Handled(self._error(action, "反射層未啟用，請改用 hold_keys／tap_keys 操作"))
        if action.kind in KNOWLEDGE_KINDS:
            return Handled(self._error(action, "知識庫功能尚未啟用，請繼續用其他動作完成任務"))
        return await self._execute(action, info, thumbnail, steps)

    async def judge_completion(self, summary: str, steps: int) -> tuple[LoopResult | None, str | None, int]:
        """模型說完成時先看畫面確認（FR-605）。

        回傳 (結束結果, 退回訊息, Decisions 次數)：通過 → 成功；沒通過 → 退回給模型繼續；
        退回超過上限仍堅持完成 → 判定失敗，不謊報成功。
        """
        checker = self._deps.completion
        if checker is None:
            return LoopResult(TaskStatus.SUCCEEDED, text=summary, steps=steps), None, 0
        shot = await self._deps.executor.screenshot()
        verdict = await checker.verify(shot.png, self._spec.instruction, summary)
        if verdict.done:
            return LoopResult(TaskStatus.SUCCEEDED, text=summary, steps=steps), None, verdict.decision_calls
        self._rejections += 1
        if self._rejections > checker.config.max_rejections:
            error = (f"AI 回報已完成，但畫面檢查 {self._rejections} 次都判斷還沒完成，所以不算成功。"
                     f"AI 的說法：{summary}")
            return LoopResult(TaskStatus.FAILED, error=error, steps=steps), None, verdict.decision_calls
        message = (f"完成檢查：從目前畫面看，任務還沒完成（完成機率 {verdict.probability or 0:.2f}）。"
                   "請繼續操作；如果真的已經完成，請在 summary 說明畫面上哪裡看得出來。")
        return None, message, verdict.decision_calls

    def _ok(self, action: Action, message: str = "", **data: Any) -> ActionOutcome:
        return ActionOutcome(action.call_id, action.kind.value, ok=True, message=message, data=data)

    def _error(self, action: Action, message: str) -> ActionOutcome:
        return ActionOutcome(action.call_id, action.kind.value, ok=False, message=message)

    async def _zoom(self, action: Action) -> Handled:
        """只看不動：重新截圖、裁出區域放大，放大圖附在 function_result 裡給模型（FR-113）。"""
        shot = await self._deps.executor.screenshot()
        box = {key: float(action.args[key]) for key in ("x1", "y1", "x2", "y2")}
        try:
            zoomed = zoom_png(shot.png, box)
        except ValueError as error:
            return Handled(self._error(action, str(error)))
        return Handled(ActionOutcome(action.call_id, action.kind.value, ok=True, message=ZOOM_MESSAGE, zoom_png=zoomed))

    async def _ask_user(self, action: Action, steps: int) -> Handled:
        control, events = self._deps.control, self._deps.events
        request_id = control.new_request_id()
        await self._deps.on_status(TaskStatus.WAITING_USER)
        await events.emit(make_event("user_help_request", task_id=self._spec.task_id, request_id=request_id,
                                     reason=str(action.args.get("reason", ""))))
        done = await control.wait_user_help(request_id)
        if control.stopped or not done:
            return Handled(self._error(action, "任務已停止"), LoopResult(TaskStatus.STOPPED, steps=steps))
        await self._deps.on_status(TaskStatus.RUNNING)
        return Handled(self._ok(action, "使用者已處理完成", user_done=True))

    async def _safety(self, action: Action, info: dict[str, str]) -> SafetyResult:
        executor = self._deps.executor
        context = SafetyContext(
            environment=executor.environment,
            current_url=info.get("url") if executor.environment is Environment.BROWSER else None,
            window_title=info.get("title") if executor.environment is Environment.DESKTOP else None,
            target_is_password=await executor.is_password_target(action),
        )
        return evaluate(action, context, self._deps.safety)

    async def _confirm(self, action: Action, reason: str, thumbnail: str) -> bool:
        control = self._deps.control
        request_id = control.new_request_id()
        await self._deps.on_status(TaskStatus.WAITING_USER)
        await self._deps.events.emit(make_event(
            "confirm_request", task_id=self._spec.task_id, request_id=request_id, action=describe(action),
            reason=reason, thumbnail=thumbnail))
        approved = await control.wait_confirmation(request_id, self._config.confirm_timeout_sec)
        if not control.stopped:
            await self._deps.on_status(TaskStatus.RUNNING)
        return approved

    async def _execute(self, action: Action, info: dict[str, str], thumbnail: str, steps: int) -> Handled:
        verdict = await self._safety(action, info)
        if verdict.verdict is Verdict.BLOCK:
            message = PASSWORD_MESSAGE if verdict.reason == "password" else f"此動作被安全規則擋下：{verdict.reason}"
            return Handled(self._error(action, message))
        acknowledged = False
        if verdict.verdict is Verdict.CONFIRM:
            approved = await self._confirm(action, verdict.reason, thumbnail)
            if self._deps.control.stopped:
                return Handled(self._error(action, "任務已停止"), LoopResult(TaskStatus.STOPPED, steps=steps))
            if not approved:
                return Handled(self._error(action, "使用者拒絕執行這個動作，請換別的做法或呼叫 ask_user"))
            acknowledged = action.safety is not None
        result = await self._deps.executor.execute(action)
        outcome = ActionOutcome(action.call_id, action.kind.value, ok=result.ok, message=result.message,
                                data=result.data, safety_acknowledged=acknowledged)
        return Handled(outcome, locator=dict(result.locator) if result.locator else None)
