from datetime import UTC, datetime, timedelta

import pytest

from familiar.core.tasks import TaskRecord, TaskStatus
from familiar.knowledge.skill_store import SkillFormatError, SkillNotFound, SkillStore
from familiar.knowledge.skills import SkillBuildError, build_steps, target_id_for
from familiar.storage.task_repository import StepRecord

NOW = datetime(2026, 11, 24, 3, 0, tzinfo=UTC)
LOCATOR = {"role": "button", "name": "送出表單", "css": "#go", "x1": 100, "y1": 100, "x2": 260, "y2": 150,
           "point": [180, 125], "frame": [1280, 800], "is_password": False}


def task(status=TaskStatus.SUCCEEDED):
    return TaskRecord(id="t1", environment="browser", target="http://127.0.0.1:8000/form.html",
                      instruction="送出表單", mode="browser", status=status, created_at=NOW)


def record(idx, kind, ok=True, locator=None, url="http://127.0.0.1:8000/form.html", **args):
    return StepRecord(task_id="t1", idx=idx, layer="agent", model="lite",
                      action={"kind": kind, "args": args, "intent": f"步驟{idx}"}, result={"ok": ok, "message": ""},
                      safety=None, screenshot_path=None, phash=None, duration_ms=1, locator=locator, page_url=url)


class TestBuildSteps:
    def test_keeps_successful_replayable_steps(self):
        records = [
            record(0, "fill_element", ref="e2", text="小明", locator={**LOCATOR, "name": "你的名字", "role": "textbox"}),
            record(1, "click", ok=False, x=1, y=1, locator=LOCATOR),
            record(2, "click_element", ref="e1", locator=LOCATOR),
            record(3, "task_done", summary="完成"),
        ]
        steps = build_steps(task(), records)
        assert [s.kind for s in steps] == ["fill_element", "click_element"]
        assert steps[0].args == {"text": "小明"}  # ref 不錄
        assert steps[1].locator.css == "#go" and steps[1].locator.point == (180, 125)
        assert steps[0].url_after == "http://127.0.0.1:8000/form.html" and steps[1].url_after is None

    @pytest.mark.parametrize("records,message", [
        ([record(0, "ask_user", reason="登入"), record(1, "click_element", ref="e1", locator=LOCATOR)], "協助"),
        ([record(0, "task_done", summary="x")], "沒有可以重播"),
        ([record(0, "click_element", ref="e1")], "定位資訊"),
    ])
    def test_rejects_unsuitable_tasks(self, records, message):
        with pytest.raises(SkillBuildError, match=message):
            build_steps(task(), records)

    def test_rejects_failed_task(self):
        with pytest.raises(SkillBuildError, match="成功"):
            build_steps(task(TaskStatus.FAILED), [record(0, "click_element", ref="e1", locator=LOCATOR)])

    def test_target_id(self):
        assert target_id_for("https://Example.com/a?b=1") == "web_example.com"
        assert target_id_for("http://127.0.0.1:8000/x") == "web_127.0.0.1_8000"


@pytest.fixture
def store(tmp_path):
    return SkillStore(tmp_path / "targets")


@pytest.fixture
def steps():
    return build_steps(task(), [record(0, "click_element", ref="e1", locator=LOCATOR)])


class TestSkillStore:
    def test_create_and_read(self, store, steps):
        meta = store.create("  送出表單  ", "送出表單", "http://127.0.0.1:8000/form.html", steps, "t1", NOW)
        assert meta.name == "送出表單" and meta.target_id == "web_127.0.0.1_8000"
        version = store.version(meta.skill_id)
        assert version.version == 1 and version.steps[0].locator.name == "送出表單"
        assert [m.skill_id for m in store.list()] == [meta.skill_id]

    def test_new_version_keeps_old_one(self, store, steps):
        meta = store.create("x", "x", "https://a.b", steps, "t1", NOW)
        updated = store.add_version(meta.skill_id, steps, "t2", NOW + timedelta(minutes=1))
        assert updated.current_version == 2
        assert store.version(meta.skill_id).source_task_id == "t2"
        assert store.version(meta.skill_id, 1).source_task_id == "t1"

    def test_results_and_relearn_flag(self, store, steps):
        meta = store.create("x", "x", "https://a.b", steps, "t1", NOW)
        for _ in range(3):
            meta = store.record_result(meta.skill_id, False, NOW)
        assert meta.fail_count == 3 and meta.needs_relearn
        meta = store.record_result(meta.skill_id, True, NOW)
        assert meta.success_count == 1 and not meta.needs_relearn

    def test_delete_and_missing(self, store, steps):
        meta = store.create("x", "x", "https://a.b", steps, "t1", NOW)
        store.delete(meta.skill_id)
        with pytest.raises(SkillNotFound):
            store.meta(meta.skill_id)
        with pytest.raises(SkillNotFound):
            store.meta("../../etc")

    def test_incompatible_file_is_rejected(self, store, steps):
        meta = store.create("x", "x", "https://a.b", steps, "t1", NOW)
        path = next((store._root).glob(f"*/skills/{meta.skill_id}/v1.json"))
        path.write_text(path.read_text(encoding="utf-8").replace('"schema_version": 1', '"schema_version": 99'),
                        encoding="utf-8")
        with pytest.raises(SkillFormatError):
            store.version(meta.skill_id)

    def test_locked_file_is_retried(self, store, steps, monkeypatch):
        """Windows 上目標檔暫時被鎖住（WinError 5）時，換名要重試而不是失敗。"""
        from pathlib import Path

        original = Path.replace
        failures = {"left": 2}

        def flaky_replace(self, target):
            if failures["left"] > 0:
                failures["left"] -= 1
                raise PermissionError(5, "存取被拒")
            return original(self, target)

        monkeypatch.setattr(Path, "replace", flaky_replace)
        meta = store.create("x", "x", "https://a.b", steps, "t1", NOW)
        assert store.meta(meta.skill_id).name == "x"
        assert not list(store._root.rglob("*.tmp"))

    def test_name_length_validated(self, store, steps):
        with pytest.raises(ValueError):
            store.create("x" * 61, "x", "https://a.b", steps, "t1", NOW)
