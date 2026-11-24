"""SQLite 連線與資料表（單人本機使用，同步存取加鎖即可）。"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Any, Sequence

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    environment TEXT NOT NULL,
    target TEXT NOT NULL,
    instruction TEXT NOT NULL,
    mode TEXT NOT NULL,
    status TEXT NOT NULL,
    result_text TEXT,
    error_message TEXT,
    steps_count INTEGER NOT NULL DEFAULT 0,
    agent_calls INTEGER NOT NULL DEFAULT 0,
    decision_calls INTEGER NOT NULL DEFAULT 0,
    skill_steps INTEGER NOT NULL DEFAULT 0,
    actor_steps INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    finished_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_tasks_created ON tasks(created_at);

CREATE TABLE IF NOT EXISTS steps (
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    idx INTEGER NOT NULL,
    layer TEXT NOT NULL,
    model TEXT,
    action_json TEXT NOT NULL,
    result_json TEXT NOT NULL,
    safety_json TEXT,
    screenshot_path TEXT,
    phash TEXT,
    duration_ms INTEGER NOT NULL DEFAULT 0,
    tokens_in INTEGER NOT NULL DEFAULT 0,
    tokens_out INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (task_id, idx)
);

CREATE TABLE IF NOT EXISTS quota_usage (
    model TEXT NOT NULL,
    pt_date TEXT NOT NULL,
    requests INTEGER NOT NULL DEFAULT 0,
    exhausted_until TEXT,
    cooldown_until TEXT,
    PRIMARY KEY (model, pt_date)
);
"""


class Database:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._conn.execute("PRAGMA foreign_keys = ON")
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    def execute(self, sql: str, params: Sequence[Any] = ()) -> int:
        with self._lock:
            cursor = self._conn.execute(sql, params)
            self._conn.commit()
            return cursor.rowcount

    def query(self, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(row) for row in self._conn.execute(sql, params).fetchall()]

    def close(self) -> None:
        with self._lock:
            self._conn.close()
