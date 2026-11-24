"""任務與步驟的存取。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from familiar.core.tasks import ACTIVE_STATUSES, TaskRecord, TaskStatus
from familiar.storage.db import Database

_TASK_COLUMNS = ("id, environment, target, instruction, mode, status, result_text, error_message, steps_count, "
                 "agent_calls, decision_calls, skill_steps, actor_steps, created_at, finished_at")
_LAYER_COUNTER = {"agent": "agent_calls", "actor": "actor_steps", "skill": "skill_steps"}


@dataclass(frozen=True)
class StepRecord:
    task_id: str
    idx: int
    layer: str
    model: str | None
    action: dict[str, Any]
    result: dict[str, Any]
    safety: dict[str, Any] | None
    screenshot_path: str | None
    phash: str | None
    duration_ms: int
    tokens_in: int = 0
    tokens_out: int = 0


def _dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _to_task(row: dict[str, Any]) -> TaskRecord:
    return TaskRecord(**{**row, "status": TaskStatus(row["status"]), "created_at": _dt(row["created_at"]),
                         "finished_at": _dt(row["finished_at"])})


def _to_step(row: dict[str, Any]) -> StepRecord:
    return StepRecord(
        task_id=row["task_id"], idx=row["idx"], layer=row["layer"], model=row["model"],
        action=json.loads(row["action_json"]), result=json.loads(row["result_json"]),
        safety=json.loads(row["safety_json"]) if row["safety_json"] else None,
        screenshot_path=row["screenshot_path"], phash=row["phash"], duration_ms=row["duration_ms"],
        tokens_in=row["tokens_in"], tokens_out=row["tokens_out"],
    )


class TaskRepository:
    def __init__(self, db: Database):
        self._db = db

    def create(self, task: TaskRecord) -> None:
        self._db.execute(
            "INSERT INTO tasks (id, environment, target, instruction, mode, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (task.id, task.environment, task.target, task.instruction, task.mode, task.status.value,
             _iso(task.created_at)),
        )

    def get(self, task_id: str) -> TaskRecord | None:
        rows = self._db.query(f"SELECT {_TASK_COLUMNS} FROM tasks WHERE id = ?", (task_id,))
        return _to_task(rows[0]) if rows else None

    def list(self, limit: int, offset: int) -> tuple[list[TaskRecord], int]:
        rows = self._db.query(f"SELECT {_TASK_COLUMNS} FROM tasks ORDER BY created_at DESC LIMIT ? OFFSET ?",
                              (limit, offset))
        total = self._db.query("SELECT COUNT(*) AS n FROM tasks")[0]["n"]
        return [_to_task(r) for r in rows], total

    def update_status(self, task_id: str, status: TaskStatus) -> None:
        self._db.execute("UPDATE tasks SET status = ? WHERE id = ?", (status.value, task_id))

    def finish(self, task_id: str, status: TaskStatus, finished_at: datetime,
               result_text: str | None = None, error_message: str | None = None) -> None:
        self._db.execute(
            "UPDATE tasks SET status = ?, result_text = ?, error_message = ?, finished_at = ? WHERE id = ?",
            (status.value, result_text, error_message, _iso(finished_at), task_id),
        )

    def add_step(self, step: StepRecord) -> None:
        self._db.execute(
            "INSERT INTO steps (task_id, idx, layer, model, action_json, result_json, safety_json, "
            "screenshot_path, phash, duration_ms, tokens_in, tokens_out) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (step.task_id, step.idx, step.layer, step.model,
             json.dumps(step.action, ensure_ascii=False), json.dumps(step.result, ensure_ascii=False),
             json.dumps(step.safety, ensure_ascii=False) if step.safety else None,
             step.screenshot_path, step.phash, step.duration_ms, step.tokens_in, step.tokens_out),
        )
        counter = _LAYER_COUNTER.get(step.layer)
        extra = f", {counter} = {counter} + 1" if counter else ""
        self._db.execute(f"UPDATE tasks SET steps_count = steps_count + 1{extra} WHERE id = ?", (step.task_id,))

    def steps(self, task_id: str) -> list[StepRecord]:
        rows = self._db.query("SELECT * FROM steps WHERE task_id = ? ORDER BY idx", (task_id,))
        return [_to_step(r) for r in rows]

    def step(self, task_id: str, idx: int) -> StepRecord | None:
        rows = self._db.query("SELECT * FROM steps WHERE task_id = ? AND idx = ?", (task_id, idx))
        return _to_step(rows[0]) if rows else None

    def fail_running(self, message: str, now: datetime) -> int:
        """後端重啟時，把上次沒結束的任務標成失敗（NFR-301）。"""
        placeholders = ", ".join("?" for _ in ACTIVE_STATUSES)
        return self._db.execute(
            f"UPDATE tasks SET status = ?, error_message = ?, finished_at = ? WHERE status IN ({placeholders})",
            (TaskStatus.FAILED.value, message, _iso(now), *(s.value for s in ACTIVE_STATUSES)),
        )
