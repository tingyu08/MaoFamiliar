"""處理模型回傳的單一函式呼叫：流程動作、安全檢查、使用者確認、交給執行器。"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from familiar.core.agent_types import LoopConfig, LoopDeps, LoopResult, TaskSpec
from familiar.core.events import make_event
from familiar.core.models import Action, ActionKind, ActionOutcome, CallError, Environment
from familiar.core.safety import SafetyContext, SafetyResult, Verdict, evaluate
from familiar.core.tasks import TaskStatus

logger = logging.getLogger(__name__)

PLANNING_KINDS = frozenset({ActionKind.COMPLETE_SUBGOAL, ActionKind.REVISE_PLAN})
KNOWLEDGE_KINDS = frozenset({ActionKind.REGISTER_STATE, ActionKind.SAVE_NOTE, ActionKind.USE_SKILL,
                             ActionKind.REQUEST_MODE})
PASSWORD_MESSAGE = "這是密碼欄位，AI 不可以輸入。請呼叫 ask_user 請使用者自己輸入。"


@dataclass(frozen=True)
class Handled:
    outcome: ActionOutcome
    finish: LoopResult | None = None
    locator: dict[str, Any] | None = None


def describe(action: Action) -> dict[str, Any]:
    return {"kind": action.kind.value, "args": dict(action.args), "intent": action.intent,
            "risk": action.risk.value}


class ActionHandler:
    def __init__(self, deps: LoopDeps, config: LoopConfig, spec: TaskSpec):
        self._deps = deps
        self._config = config
        self._spec = spec

    async def handle(self, item: Action | CallError, info: dict[str, str], thumbnail: str, steps: int) -> Handled:
        if isinstance(item, CallError):
            return Handled(ActionOutcome(item.call_id, item.name, ok=False, message=item.message))
        action = item
        if action.kind is ActionKind.TASK_DONE:
            summary = str(action.args.get("summary", ""))
            return Handled(self._ok(action), LoopResult(TaskStatus.SUCCEEDED, text=summary, steps=steps))
        if action.kind is ActionKind.ASK_USER:
            return await self._ask_user(action, steps)
        if action.kind in PLANNING_KINDS:
            await self._deps.events.emit(make_event("plan_updated", task_id=self._spec.task_id, **describe(action)))
            return Handled(self._ok(action))
        if action.kind in KNOWLEDGE_KINDS:
            return Handled(self._error(action, "知識庫功能尚未啟用，請繼續用其他動作完成任務"))
        return await self._execute(action, info, thumbnail, steps)

    def _ok(self, action: Action, message: str = "", **data: Any) -> ActionOutcome:
        return ActionOutcome(action.call_id, action.kind.value, ok=True, message=message, data=data)

    def _error(self, action: Action, message: str) -> ActionOutcome:
        return ActionOutcome(action.call_id, action.kind.value, ok=False, message=message)

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
