from datetime import UTC, date, datetime, timedelta

import pytest

from familiar.core.tasks import TaskRecord, TaskStatus
from familiar.gemini.quota import ModelUsage
from familiar.storage.artifact_store import ArtifactStore
from familiar.storage.db import Database
from familiar.storage.quota_repository import QuotaRepository
from familiar.storage.task_repository import StepRecord, TaskRepository

NOW = datetime(2026, 11, 23, 8, 0, tzinfo=UTC)


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "test.db")
    yield database
    database.close()


def make_task(task_id="t1", status=TaskStatus.RUNNING):
    return TaskRecord(id=task_id, environment="browser", target="https://example.com",
                      instruction="找公告", mode="browser", status=status, created_at=NOW)


def make_step(idx, layer="agent"):
    return StepRecord(task_id="t1", idx=idx, layer=layer, model="lite", action={"kind": "click", "x": 1},
                      result={"ok": True}, safety=None, screenshot_path=f"t1/{idx}.png", phash="ab",
                      duration_ms=120, tokens_in=1000, tokens_out=20)


class TestTaskRepository:
    def test_create_and_get(self, db):
        repo = TaskRepository(db)
        repo.create(make_task())
        task = repo.get("t1")
        assert task.instruction == "找公告" and task.status is TaskStatus.RUNNING
        assert task.created_at == NOW

    def test_get_missing(self, db):
        assert TaskRepository(db).get("nope") is None

    def test_steps_update_counters(self, db):
        repo = TaskRepository(db)
        repo.create(make_task())
        repo.add_step(make_step(0))
        repo.add_step(make_step(1, layer="actor"))
        task = repo.get("t1")
        assert task.steps_count == 2 and task.agent_calls == 1 and task.actor_steps == 1
        steps = repo.steps("t1")
        assert [s.idx for s in steps] == [0, 1]
        assert steps[0].action == {"kind": "click", "x": 1}

    def test_finish(self, db):
        repo = TaskRepository(db)
        repo.create(make_task())
        repo.finish("t1", TaskStatus.SUCCEEDED, result_text="完成", finished_at=NOW + timedelta(minutes=1))
        task = repo.get("t1")
        assert task.status is TaskStatus.SUCCEEDED and task.result_text == "完成"
        assert task.finished_at == NOW + timedelta(minutes=1)

    def test_list_paginates_newest_first(self, db):
        repo = TaskRepository(db)
        for i in range(3):
            repo.create(TaskRecord(id=f"t{i}", environment="browser", target="u", instruction="x",
                                   mode="browser", status=TaskStatus.SUCCEEDED, created_at=NOW + timedelta(seconds=i)))
        items, total = repo.list(limit=2, offset=0)
        assert total == 3 and [t.id for t in items] == ["t2", "t1"]

    def test_fail_running_on_restart(self, db):
        repo = TaskRepository(db)
        repo.create(make_task("a", TaskStatus.RUNNING))
        repo.create(make_task("b", TaskStatus.SUCCEEDED))
        assert repo.fail_running("後端重新啟動", NOW) == 1
        assert repo.get("a").status is TaskStatus.FAILED
        assert repo.get("b").status is TaskStatus.SUCCEEDED

    def test_update_status(self, db):
        repo = TaskRepository(db)
        repo.create(make_task())
        repo.update_status("t1", TaskStatus.WAITING_USER)
        assert repo.get("t1").status is TaskStatus.WAITING_USER


class TestQuotaRepository:
    def test_round_trip_for_today(self, db):
        repo = QuotaRepository(db)
        usage = ModelUsage(pt_date=date(2026, 11, 23), day_requests=7,
                           exhausted_until=NOW + timedelta(hours=1))
        repo.save("lite", usage)
        loaded = repo.load(date(2026, 11, 23))
        assert loaded["lite"].day_requests == 7
        assert loaded["lite"].exhausted_until == NOW + timedelta(hours=1)
        assert loaded["lite"].minute_events == ()

    def test_other_days_not_loaded(self, db):
        repo = QuotaRepository(db)
        repo.save("lite", ModelUsage(pt_date=date(2026, 11, 22), day_requests=3))
        assert repo.load(date(2026, 11, 23)) == {}

    def test_save_overwrites(self, db):
        repo = QuotaRepository(db)
        repo.save("lite", ModelUsage(pt_date=date(2026, 11, 23), day_requests=1))
        repo.save("lite", ModelUsage(pt_date=date(2026, 11, 23), day_requests=2))
        assert repo.load(date(2026, 11, 23))["lite"].day_requests == 2


class TestArtifactStore:
    def test_save_and_read(self, tmp_path):
        store = ArtifactStore(tmp_path / "shots")
        path = store.save_screenshot("t1", 3, b"png-bytes")
        assert store.read(path) == b"png-bytes"

    def test_rejects_unsafe_ids(self, tmp_path):
        store = ArtifactStore(tmp_path / "shots")
        with pytest.raises(ValueError):
            store.save_screenshot("../evil", 0, b"x")

    def test_read_outside_root_rejected(self, tmp_path):
        store = ArtifactStore(tmp_path / "shots")
        with pytest.raises(ValueError):
            store.read("../../secret.txt")

    def test_delete_task(self, tmp_path):
        store = ArtifactStore(tmp_path / "shots")
        path = store.save_screenshot("t1", 0, b"x")
        store.delete_task("t1")
        with pytest.raises(FileNotFoundError):
            store.read(path)
