"""任務生命週期：一次只跑一個任務、存檔、發事件、處理停止與使用者回應（SDD 3.3）。"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Awaitable, Callable
from urllib.parse import urlparse

from familiar.core.agent_loop import AgentLoop
from familiar.core.agent_types import LoopResult, StepInfo, TaskSpec
from familiar.core.control import TaskControl
from familiar.core.events import EventSink, make_event
from familiar.core.models import Environment, OperatingMode
from familiar.core.tasks import TaskRecord, TaskStatus
from familiar.executors.base import Executor
from familiar.knowledge.replay import ReplayContext, run_replay
from familiar.storage.artifact_store import ArtifactStore
from familiar.storage.task_repository import StepRecord, TaskRepository

logger = logging.getLogger(__name__)

MAX_INSTRUCTION = 2000
OnStep = Callable[[StepInfo], Awaitable[None]]
OnStatus = Callable[[TaskStatus], Awaitable[None]]
LoopFactory = Callable[[Executor, TaskControl, OnStep, OnStatus], AgentLoop]
ExecutorFactory = Callable[[Environment], Executor]
RunBody = Callable[[AgentLoop, TaskSpec], Awaitable[LoopResult]]


class TaskBusyError(Exception):
    """已經有任務在執行（FR-004）。"""


class InvalidTaskError(ValueError):
    pass


@dataclass(frozen=True)
class _Running:
    task_id: str
    control: TaskControl
    job: asyncio.Task


def validate_request(url: str, instruction: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise InvalidTaskError("網址必須是 http:// 或 https:// 開頭")
    if not instruction.strip() or len(instruction) > MAX_INSTRUCTION:
        raise InvalidTaskError(f"任務描述需要 1–{MAX_INSTRUCTION} 字")


class TaskRunner:
    def __init__(self, repo: TaskRepository, artifacts: ArtifactStore, events: EventSink,
                 executor_factory: ExecutorFactory, loop_factory: LoopFactory, clock: Callable[[], datetime],
                 replay: ReplayContext | None = None):
        self._repo = repo
        self._artifacts = artifacts
        self._events = events
        self._executor_factory = executor_factory
        self._loop_factory = loop_factory
        self._clock = clock
        self._replay = replay
        self._current: _Running | None = None

    @property
    def current_task_id(self) -> str | None:
        running = self._current
        return running.task_id if running and not running.job.done() else None

    def recover(self) -> int:
        """後端啟動時呼叫：上次沒結束的任務標為失敗（NFR-301）。"""
        return self._repo.fail_running("後端重新啟動，任務中斷", self._clock())

    async def start(self, url: str, instruction: str, mode: OperatingMode = OperatingMode.UI) -> TaskRecord:
        """mode=REALTIME 是遊戲模式：可以按住按鍵、卡住偵測只看遊戲畫面。"""
        validate_request(url, instruction)
        return await self._launch(url, instruction.strip(), "realtime" if mode is OperatingMode.REALTIME else "browser",
                                  None, lambda loop, spec: loop.run(spec), mode)

    async def start_replay(self, skill_id: str) -> TaskRecord:
        """重播技能（第 1 層）；失敗時自動交給代理人修復（FR-303、FR-404）。"""
        if self._replay is None:
            raise InvalidTaskError("沒有啟用技能功能")
        meta = self._replay.skills.meta(skill_id)
        version = self._replay.skills.version(skill_id)
        ctx = self._replay

        def body(loop: AgentLoop, spec: TaskSpec) -> Awaitable[LoopResult]:
            return run_replay(ctx, loop, spec, version, meta.instruction)

        return await self._launch(meta.start_url, f"重播技能：{meta.name}", "replay", skill_id, body)

    async def _launch(self, url: str, instruction: str, mode: str, skill_id: str | None, body: RunBody,
                      operating: OperatingMode = OperatingMode.UI) -> TaskRecord:
        if self.current_task_id:
            raise TaskBusyError("已經有任務在執行，請先停止或等它結束")
        record = TaskRecord(id=uuid.uuid4().hex[:16], environment=Environment.BROWSER.value, target=url,
                            instruction=instruction, mode=mode, status=TaskStatus.RUNNING,
                            created_at=self._clock(), skill_id=skill_id)
        self._repo.create(record)
        control = TaskControl()
        spec = TaskSpec(task_id=record.id, instruction=record.instruction, environment=Environment.BROWSER,
                        mode=operating)
        job = asyncio.create_task(self._run(spec, url, control, body))
        self._current = _Running(record.id, control, job)
        await self._events.emit(make_event("task_started", task_id=record.id, target=url,
                                           instruction=record.instruction,
                                           mode="replay" if skill_id else "agent", skill_id=skill_id,
                                           realtime=operating is OperatingMode.REALTIME))
        return record

    def stop(self, task_id: str) -> bool:
        running = self._current
        if not running or running.task_id != task_id or running.job.done():
            return False
        running.control.request_stop()
        return True

    def confirm(self, request_id: str, approved: bool) -> bool:
        running = self._current
        return bool(running and running.control.resolve_confirmation(request_id, approved))

    def user_help_done(self, request_id: str) -> bool:
        running = self._current
        return bool(running and running.control.resolve_user_help(request_id))

    async def wait_current(self) -> None:
        if self._current:
            await self._current.job

    async def shutdown(self) -> None:
        running = self._current
        if running and not running.job.done():
            running.control.request_stop()
            await asyncio.wait({running.job}, timeout=10)

    async def _run(self, spec: TaskSpec, url: str, control: TaskControl, body: RunBody) -> None:
        executor = self._executor_factory(spec.environment)
        try:
            result = await self._execute(spec, url, control, executor, body)
        finally:
            try:
                await executor.close()
            except Exception:  # noqa: BLE001 — 關閉失敗不影響任務結果
                logger.warning("關閉執行器失敗", exc_info=True)
        self._repo.finish(spec.task_id, result.status, self._clock(), result_text=result.text,
                          error_message=result.error)
        await self._events.emit(make_event("task_finished", task_id=spec.task_id, status=result.status.value,
                                           result_text=result.text, error=result.error, steps=result.steps))

    async def _execute(self, spec: TaskSpec, url: str, control: TaskControl, executor: Executor,
                       body: RunBody) -> LoopResult:
        try:
            await executor.start(url)
        except Exception:  # noqa: BLE001
            logger.exception("無法開啟目標 %s", url)
            return LoopResult(TaskStatus.FAILED, error="無法開啟網址，請確認網址是否正確、網路是否正常")
        loop = self._loop_factory(executor, control, self._step_saver(spec.task_id), self._status_saver(spec.task_id))
        try:
            return await body(loop, spec)
        except Exception:  # noqa: BLE001 — 最外層：任何未預期錯誤都要讓任務正常結束
            logger.exception("任務 %s 發生未預期的錯誤", spec.task_id)
            return LoopResult(TaskStatus.FAILED, error="發生未預期的錯誤（詳細內容請看後端日誌）")

    def _step_saver(self, task_id: str) -> OnStep:
        async def save(info: StepInfo) -> None:
            path = self._artifacts.save_screenshot(task_id, info.index, info.screenshot_png)
            self._repo.add_step(StepRecord(
                task_id=task_id, idx=info.index, layer=info.layer, model=info.model, action=info.action,
                result={"ok": info.ok, "message": info.message}, safety=info.safety, screenshot_path=path,
                phash=info.phash, duration_ms=info.duration_ms, tokens_in=info.tokens_in,
                tokens_out=info.tokens_out, locator=info.locator, page_url=info.page_url,
                decision_calls=info.decision_calls))
        return save

    def _status_saver(self, task_id: str) -> OnStatus:
        async def save(status: TaskStatus) -> None:
            self._repo.update_status(task_id, status)
            await self._events.emit(make_event("task_status", task_id=task_id, status=status.value))
        return save
