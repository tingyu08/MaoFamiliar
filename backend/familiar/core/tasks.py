"""任務的狀態與紀錄型別。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class TaskStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING_USER = "waiting_user"
    WAITING_QUOTA = "waiting_quota"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    STOPPED = "stopped"

    @property
    def is_final(self) -> bool:
        return self in (TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.STOPPED)


ACTIVE_STATUSES = (TaskStatus.PENDING, TaskStatus.RUNNING, TaskStatus.WAITING_USER, TaskStatus.WAITING_QUOTA)


@dataclass(frozen=True)
class TaskRecord:
    id: str
    environment: str
    target: str
    instruction: str
    mode: str
    status: TaskStatus
    created_at: datetime
    result_text: str | None = None
    error_message: str | None = None
    steps_count: int = 0
    agent_calls: int = 0
    decision_calls: int = 0
    skill_steps: int = 0
    actor_steps: int = 0
    finished_at: datetime | None = None
    skill_id: str | None = None
