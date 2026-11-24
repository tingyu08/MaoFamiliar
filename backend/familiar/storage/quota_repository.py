"""配額用量持久化：後端重啟後，今日已用次數與停用狀態不會遺失。每分鐘事件不保存。"""

from __future__ import annotations

from datetime import date, datetime

from familiar.gemini.quota import ModelUsage
from familiar.storage.db import Database


def _dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


class QuotaRepository:
    def __init__(self, db: Database):
        self._db = db

    def load(self, today: date) -> dict[str, ModelUsage]:
        rows = self._db.query("SELECT * FROM quota_usage WHERE pt_date = ?", (today.isoformat(),))
        return {
            row["model"]: ModelUsage(pt_date=today, day_requests=row["requests"],
                                     exhausted_until=_dt(row["exhausted_until"]),
                                     cooldown_until=_dt(row["cooldown_until"]))
            for row in rows
        }

    def save(self, model: str, usage: ModelUsage) -> None:
        self._db.execute(
            "INSERT INTO quota_usage (model, pt_date, requests, exhausted_until, cooldown_until) "
            "VALUES (?, ?, ?, ?, ?) ON CONFLICT(model, pt_date) DO UPDATE SET requests = excluded.requests, "
            "exhausted_until = excluded.exhausted_until, cooldown_until = excluded.cooldown_until",
            (model, usage.pt_date.isoformat(), usage.day_requests,
             usage.exhausted_until.isoformat() if usage.exhausted_until else None,
             usage.cooldown_until.isoformat() if usage.cooldown_until else None),
        )
