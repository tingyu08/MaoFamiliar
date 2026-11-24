from datetime import UTC, datetime

import pytest
from agent_fakes import FakeExecutor, RecordingSink, ScriptedClient, call, later, turn

from familiar.backends.custom_fc import CustomFcBackend
from familiar.core.agent_loop import AgentLoop
from familiar.core.agent_types import LoopDeps
from familiar.core.models import ActionKind
from familiar.core.safety import SafetyConfig
from familiar.core.task_runner import TaskRunner
from familiar.core.tasks import TaskStatus
from familiar.executors.elements import Element
from familiar.gemini.model_router import ModelRouter, Role, RouterConfig
from familiar.knowledge.replay import ReplayContext, heal_instruction
from familiar.knowledge.skill_store import SkillStore
from familiar.knowledge.skills import Locator, SkillStep
from familiar.storage.artifact_store import ArtifactStore
from familiar.storage.db import Database
from familiar.storage.task_repository import TaskRepository

URL = "https://example.test/form"
NAME = Element("e1", "textbox", "你的名字", 10, 10, 110, 30, css="#name")
SUBMIT = Element("e2", "button", "送出", 10, 50, 60, 80, css="#go")

FILL = SkillStep(kind="fill_element", args={"text": "小明"}, intent="填名字",
                 locator=Locator(role="textbox", name="你的名字", css="#name", point=(60, 20), frame=(320, 200)),
                 page_url=URL, url_after=URL)
CLICK = SkillStep(kind="click_element", intent="按送出",
                  locator=Locator(role="button", name="送出", css="#go", point=(35, 65), frame=(640, 400)),
                  page_url=URL)


def now():
    return datetime.now(UTC)


class Harness:
    def __init__(self, tmp_path, client=None, executor=None, on_event=None):
        db = Database(tmp_path / "t.db")
        self.repo = TaskRepository(db)
        self.skills = SkillStore(tmp_path / "targets")
        self.sink = RecordingSink(on_event)
        self.executor = executor or FakeExecutor(elements=(NAME, SUBMIT))
        self.client = client or ScriptedClient()

        async def no_sleep(_):
            return None

        router = ModelRouter(RouterConfig(chains={Role.ACTION: ("lite",)}), clock=now, sleep=no_sleep)

        def loop_factory(executor, control, on_step, on_status):
            return AgentLoop(LoopDeps(router=router, client=self.client, backend=CustomFcBackend(),
                                      executor=executor, events=self.sink, control=control, safety=SafetyConfig(),
                                      clock=now, on_step=on_step, on_status=on_status))

        self.runner = TaskRunner(self.repo, ArtifactStore(tmp_path / "shots"), self.sink, lambda env: self.executor,
                                 loop_factory, clock=now, replay=ReplayContext(self.skills, self.repo, now))

    def skill(self, *steps):
        return self.skills.create("送出表單", "填名字並送出", URL, steps, "t0", now())

    async def replay(self, skill_id):
        record = await self.runner.start_replay(skill_id)
        await self.runner.wait_current()
        return self.repo.get(record.id), self.repo.steps(record.id)


async def test_replay_without_ai(tmp_path):
    h = Harness(tmp_path)
    meta = h.skill(FILL, CLICK)
    task, steps = await h.replay(meta.skill_id)
    assert task.status is TaskStatus.SUCCEEDED and "沒有呼叫 AI" in task.result_text
    assert task.mode == "replay" and task.skill_id == meta.skill_id and task.skill_steps == 2
    assert task.agent_calls == 0 and h.client.requests == []
    assert [(a.kind, a.args.get("ref")) for a in h.executor.executed] == [
        (ActionKind.FILL_ELEMENT, "e1"), (ActionKind.CLICK_ELEMENT, "e2")]
    assert h.executor.executed[0].args["text"] == "小明"
    assert all(s.layer == "skill" for s in steps) and "role＋名稱" in steps[0].result["message"]
    assert h.skills.meta(meta.skill_id).success_count == 1
    assert h.executor.started == [URL]


async def test_falls_back_to_css_then_point(tmp_path):
    renamed = Element("e5", "button", "提交", 10, 50, 60, 80, css="#go")
    h = Harness(tmp_path, executor=FakeExecutor(elements=(renamed,), css_refs={"#go": "e0"}))
    meta = h.skill(CLICK)
    _, steps = await h.replay(meta.skill_id)
    assert h.executor.executed[0].args["ref"] == "e0" and "CSS" in steps[0].result["message"]

    # 名稱和 CSS 都變了，但同一個位置還是按鈕 → 用座標點
    moved_name = Element("e7", "button", "Submit", 10, 20, 40, 50)
    h2 = Harness(tmp_path / "b", executor=FakeExecutor(elements=(moved_name,)))
    meta2 = h2.skill(CLICK)
    await h2.replay(meta2.skill_id)
    action = h2.executor.executed[0]
    assert action.kind is ActionKind.CLICK
    assert (action.args["x"], action.args["y"]) == (18, 32)  # 錄製時 640×400 → 現在 320×200


async def test_point_fallback_refuses_when_layout_changed(tmp_path):
    h = Harness(tmp_path, executor=FakeExecutor(elements=()))  # 原位置上已經沒有按鈕
    meta = h.skill(CLICK)
    task, steps = await h.replay(meta.skill_id)
    assert h.executor.executed == []  # 不會盲點
    assert "找不到「送出」" in steps[0].result["message"]
    assert task.status is TaskStatus.FAILED  # 沒有劇本的代理人修不好


async def test_failure_hands_over_to_agent_and_saves_new_version(tmp_path):
    client = ScriptedClient(turn(call("click_element", ref="e2", intent="改按新的送出按鈕")),
                            turn(call("task_done", "c2", summary="已送出")))
    h = Harness(tmp_path, client=client, executor=FakeExecutor(elements=(SUBMIT,)))  # 名字欄位不見了
    meta = h.skill(FILL, CLICK)
    task, steps = await h.replay(meta.skill_id)
    assert task.status is TaskStatus.SUCCEEDED
    assert "第 2 版" in task.result_text
    assert [s.layer for s in steps] == ["skill", "skill", "agent", "agent"]  # 重播失敗（含重試一次）→ 代理人接手
    assert not steps[0].result["ok"] and "找不到「你的名字」" in steps[0].result["message"]
    assert "原本的任務：填名字並送出" in client.requests[0]["input"][0]["text"]
    healed = h.skills.version(meta.skill_id)
    assert healed.version == 2 and [s.kind for s in healed.steps] == ["click_element"]
    assert "skill_heal" in h.sink.types()


async def test_failed_heal_counts_as_failure(tmp_path):
    h = Harness(tmp_path, client=ScriptedClient(turn(call("click", x=1, y=1)), turn(text="")),
                executor=FakeExecutor(elements=(), failing_kinds=frozenset({"click"})))
    meta = h.skill(FILL)
    h.client.script = [RuntimeError("model down")]
    task, _ = await h.replay(meta.skill_id)
    assert task.status is TaskStatus.FAILED
    assert h.skills.meta(meta.skill_id).fail_count == 1


async def test_high_risk_step_still_needs_confirmation(tmp_path):
    def approve(event):
        if event["type"] == "confirm_request":
            later(lambda: h.runner.confirm(event["request_id"], True))

    h = Harness(tmp_path, on_event=approve)
    meta = h.skill(CLICK.model_copy(update={"risk": "high"}))
    task, _ = await h.replay(meta.skill_id)
    assert task.status is TaskStatus.SUCCEEDED
    assert "confirm_request" in h.sink.types()


async def test_stop_during_replay(tmp_path):
    def stop(event):
        if event["type"] == "confirm_request":
            later(lambda: h.runner.stop(event["task_id"]))

    h = Harness(tmp_path, on_event=stop)
    meta = h.skill(CLICK.model_copy(update={"risk": "high"}), FILL)
    task, _ = await h.replay(meta.skill_id)
    assert task.status is TaskStatus.STOPPED
    assert h.executor.executed == []


async def test_navigation_check(tmp_path):
    client = ScriptedClient(turn(call("task_done", summary="完成")))
    h = Harness(tmp_path, client=client)
    meta = h.skill(CLICK.model_copy(update={"url_after": "https://example.test/done"}))
    task, steps = await h.replay(meta.skill_id)
    assert any("預期的頁面" in s.result["message"] for s in steps if s.layer == "skill")
    assert task.status is TaskStatus.SUCCEEDED and len(client.requests) == 1


async def test_unknown_skill(tmp_path):
    from familiar.knowledge.skill_store import SkillNotFound

    with pytest.raises(SkillNotFound):
        await Harness(tmp_path).runner.start_replay("0123456789ab")


def test_heal_instruction_lists_progress():
    from familiar.knowledge.skills import SkillVersion

    version = SkillVersion(skill_id="a" * 12, version=1, start_url=URL, steps=(FILL, CLICK), created_at=now())
    text = heal_instruction("填名字並送出", version, 1, "找不到「送出」")
    assert "第 2 步失敗" in text and "已經完成的步驟：填名字" in text and "按送出" in text
