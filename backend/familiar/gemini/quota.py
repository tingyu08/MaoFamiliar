"""每個模型的配額狀態（純函式，不可變資料）。

- 每日次數：以太平洋時間的日期為單位，午夜歸零（官方規則）。
- 每分鐘次數與 token：保留最近 60 秒的事件做滑動視窗。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, time, timedelta
from enum import StrEnum
from zoneinfo import ZoneInfo

PACIFIC = ZoneInfo("America/Los_Angeles")
WINDOW = timedelta(seconds=60)


class Status(StrEnum):
    READY = "ready"
    WAIT = "wait"                # 每分鐘上限，稍等即可
    UNAVAILABLE = "unavailable"  # 今日用完、冷卻中


@dataclass(frozen=True)
class ModelLimits:
    """0 代表未知／不限制。"""

    rpm: int = 0
    rpd: int = 0
    tpm: int = 0


@dataclass(frozen=True)
class ModelUsage:
    pt_date: date
    day_requests: int = 0
    minute_events: tuple[tuple[datetime, int], ...] = ()  # (時間, 輸入 token)
    exhausted_until: datetime | None = None
    cooldown_until: datetime | None = None
    overload_streak: int = 0  # 連續過載次數；成功或換日歸零


@dataclass(frozen=True)
class Availability:
    status: Status
    ready_at: datetime | None = None
    reason: str = ""


def _require_aware(moment: datetime) -> None:
    if moment.tzinfo is None:
        raise ValueError("時間必須帶時區（UTC）")


def pacific_date(moment: datetime) -> date:
    _require_aware(moment)
    return moment.astimezone(PACIFIC).date()


def next_pacific_midnight(moment: datetime) -> datetime:
    tomorrow = pacific_date(moment) + timedelta(days=1)
    return datetime.combine(tomorrow, time(0), tzinfo=PACIFIC).astimezone(UTC)


def new_usage(now: datetime) -> ModelUsage:
    return ModelUsage(pt_date=pacific_date(now))


def roll(usage: ModelUsage, now: datetime) -> ModelUsage:
    """換日歸零、移除超過 60 秒的事件、清除已過期的停用標記。"""
    today = pacific_date(now)
    events = tuple(e for e in usage.minute_events if now - e[0] < WINDOW)
    exhausted = usage.exhausted_until if usage.exhausted_until and usage.exhausted_until > now else None
    cooldown = usage.cooldown_until if usage.cooldown_until and usage.cooldown_until > now else None
    same_day = today == usage.pt_date
    return ModelUsage(today, usage.day_requests if same_day else 0, events, exhausted, cooldown,
                      usage.overload_streak if same_day else 0)


def record(usage: ModelUsage, now: datetime, tokens: int = 0) -> ModelUsage:
    """記錄一次成功的請求；成功代表模型恢復，連續過載次數歸零。"""
    current = roll(usage, now)
    return replace(current, day_requests=current.day_requests + 1, overload_streak=0,
                   minute_events=(*current.minute_events, (now, max(0, tokens))))


def mark_overloaded(usage: ModelUsage, now: datetime, initial: timedelta, maximum: timedelta) -> ModelUsage:
    """過載冷卻逐步加長：initial、2×、4×…，上限 maximum。"""
    current = roll(usage, now)
    streak = current.overload_streak + 1
    cooldown = min(initial * (2 ** (streak - 1)), maximum)
    return replace(current, overload_streak=streak, cooldown_until=now + cooldown)


def mark_exhausted(usage: ModelUsage, until: datetime) -> ModelUsage:
    return replace(usage, exhausted_until=until)


def mark_cooldown(usage: ModelUsage, until: datetime) -> ModelUsage:
    return replace(usage, cooldown_until=until)


def _token_wait(events: tuple[tuple[datetime, int], ...], limit: int, est_tokens: int) -> datetime | None:
    """要等到哪個事件過期，剩餘 token 才放得下這次請求；放得下就回傳 None。"""
    used = sum(tokens for _, tokens in events)
    for moment, tokens in events:
        if used + est_tokens <= limit:
            return None
        used -= tokens
        if used + est_tokens <= limit:
            return moment + WINDOW
    return None  # 單次請求就超過上限：無法靠等待解決，直接送


def availability(usage: ModelUsage, limits: ModelLimits, now: datetime, est_tokens: int = 0) -> Availability:
    current = roll(usage, now)
    if current.exhausted_until:
        return Availability(Status.UNAVAILABLE, current.exhausted_until, "今日額度已用完")
    if current.cooldown_until:
        return Availability(Status.UNAVAILABLE, current.cooldown_until, "冷卻中")
    if limits.rpd and current.day_requests >= limits.rpd:
        return Availability(Status.UNAVAILABLE, next_pacific_midnight(now), "已達每日上限")
    events = current.minute_events
    if limits.rpm and len(events) >= limits.rpm:
        return Availability(Status.WAIT, events[len(events) - limits.rpm][0] + WINDOW, "已達每分鐘次數上限")
    if limits.tpm:
        ready_at = _token_wait(events, limits.tpm, est_tokens)
        if ready_at:
            return Availability(Status.WAIT, ready_at, "已達每分鐘 token 上限")
    return Availability(Status.READY)
