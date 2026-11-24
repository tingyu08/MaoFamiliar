import asyncio
from datetime import UTC, datetime

import pytest
from agent_fakes import FakeExecutor, RecordingSink, ScriptedClient, call, later, turn

from familiar.backends.custom_fc import CustomFcBackend
from familiar.core.agent_loop import AgentLoop
from familiar.core.agent_types import LoopDeps
from familiar.core.safety import SafetyConfig
from familiar.core.task_runner import InvalidTaskError, TaskBusyError, TaskRunner
from familiar.core.tasks import TaskRecord, TaskStatus
from familiar.gemini.model_router import ModelRouter, Role, RouterConfig
from familiar.storage.artifact_store import ArtifactStore
from familiar.storage.db import Database
from familiar.storage.task_repository import TaskRepository

URL = "https://example.test/form"


class Env:
    def __init__(self, tmp_path, client, executor=None, on_event=None, start_error=None, sink=None):
        self.db = Database(tmp_path / "t.db")
        self.repo = TaskRepository(self.db)
        self.artifacts = ArtifactStore(tmp_path / "shots")
        self.sink = sink or RecordingSink(on_event)
        self.executor = executor or FakeExecutor()
        if start_error:
            async def failing_start(target):
                raise start_error
            self.executor.start = failing_start

        async def no_sleep(_):
            return None

        router = ModelRouter(RouterConfig(chains={Role.ACTION: ("lite",)}), clock=lambda: datetime.now(UTC),
                             sleep=no_sleep)

        def loop_factory(executor, control, on_step, on_status):
            deps = LoopDeps(router=router, client=client, backend=CustomFcBackend(), executor=executor,
                            events=self.sink, control=control, safety=SafetyConfig(),
                            clock=lambda: datetime.now(UTC), on_step=on_step, on_status=on_status)
            return AgentLoop(deps)

        self.runner = TaskRunner(self.repo, self.artifacts, self.sink, lambda env: self.executor, loop_factory,
                                 clock=lambda: datetime.now(UTC))


async def test_successful_task_is_persisted(tmp_path):
    env = Env(tmp_path, ScriptedClient(turn(call("click_element", ref="e1")), turn(call("task_done", "c2", summary="完成"))))
    record = await env.runner.start(URL, "  送出表單  ")
    assert record.instruction == "送出表單"
    await env.runner.wait_current()
    saved = env.repo.get(record.id)
    assert saved.status is TaskStatus.SUCCEEDED and saved.result_text == "完成"
    assert saved.steps_count == 2 and saved.agent_calls == 2
    steps = env.repo.steps(record.id)
    assert env.artifacts.read(steps[0].screenshot_path).startswith(b"\x89PNG")
    assert env.sink.types()[0] == "task_started" and env.sink.types()[-1] == "task_finished"
    assert env.runner.current_task_id is None


async def test_rejects_second_task_while_running(tmp_path):
    env = Env(tmp_path, ScriptedClient(turn(call("ask_user", reason="等等"))))
    first = await env.runner.start(URL, "a")
    await asyncio.sleep(0.05)
    with pytest.raises(TaskBusyError):
        await env.runner.start(URL, "b")
    assert env.runner.stop(first.id)
    await env.runner.wait_current()
    assert env.repo.get(first.id).status is TaskStatus.STOPPED


@pytest.mark.parametrize("url,instruction", [("javascript:alert(1)", "x"), ("ftp://a.b", "x"),
                                             (URL, "   "), (URL, "x" * 2001)])
async def test_invalid_requests(tmp_path, url, instruction):
    env = Env(tmp_path, ScriptedClient())
    with pytest.raises(InvalidTaskError):
        await env.runner.start(url, instruction)


async def test_confirmation_routed_to_running_task(tmp_path):
    def approve(event):
        if event["type"] == "confirm_request":
            later(lambda: env.runner.confirm(event["request_id"], True))

    env = Env(tmp_path, ScriptedClient(turn(call("click", x=1, y=1, risk="high")), turn(text="ok")), on_event=approve)
    record = await env.runner.start(URL, "a")
    await env.runner.wait_current()
    assert env.repo.get(record.id).status is TaskStatus.SUCCEEDED
    assert "task_status" in env.sink.types()


async def test_user_help_routed(tmp_path):
    def done(event):
        if event["type"] == "user_help_request":
            later(lambda: env.runner.user_help_done(event["request_id"]))

    env = Env(tmp_path, ScriptedClient(turn(call("ask_user", reason="登入")), turn(text="ok")), on_event=done)
    record = await env.runner.start(URL, "a")
    await env.runner.wait_current()
    assert env.repo.get(record.id).status is TaskStatus.SUCCEEDED


async def test_executor_start_failure(tmp_path):
    env = Env(tmp_path, ScriptedClient(), start_error=RuntimeError("net::ERR_NAME_NOT_RESOLVED"))
    record = await env.runner.start(URL, "a")
    await env.runner.wait_current()
    saved = env.repo.get(record.id)
    assert saved.status is TaskStatus.FAILED and "無法開啟網址" in saved.error_message


async def test_unexpected_loop_error(tmp_path):
    env = Env(tmp_path, ScriptedClient())

    async def broken_screenshot():
        raise RuntimeError("boom")

    env.executor.screenshot = broken_screenshot
    record = await env.runner.start(URL, "a")
    await env.runner.wait_current()
    assert "未預期" in env.repo.get(record.id).error_message


async def test_recover_and_misc(tmp_path):
    env = Env(tmp_path, ScriptedClient())
    env.repo.create(TaskRecord(id="old", environment="browser", target=URL, instruction="x", mode="browser",
                               status=TaskStatus.RUNNING, created_at=datetime.now(UTC)))
    assert env.runner.recover() == 1
    assert not env.runner.stop("nope")
    assert not env.runner.confirm("r", True) and not env.runner.user_help_done("r")
    await env.runner.shutdown()


async def test_shutdown_stops_running_task(tmp_path):
    env = Env(tmp_path, ScriptedClient(turn(call("ask_user", reason="等"))))
    record = await env.runner.start(URL, "a")
    await asyncio.sleep(0.05)
    await env.runner.shutdown()
    assert env.repo.get(record.id).status is TaskStatus.STOPPED
