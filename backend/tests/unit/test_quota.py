from datetime import UTC, date, datetime, timedelta

import pytest

from familiar.gemini.quota import (
    ModelLimits,
    Status,
    availability,
    mark_cooldown,
    mark_exhausted,
    mark_overloaded,
    new_usage,
    next_pacific_midnight,
    pacific_date,
    record,
    roll,
)

T0 = datetime(2026, 11, 23, 7, 30, tzinfo=UTC)  # 太平洋時間 11/22 23:30


class TestPacificTime:
    def test_date_before_and_after_midnight(self):
        assert pacific_date(T0) == date(2026, 11, 22)
        assert pacific_date(T0 + timedelta(hours=1)) == date(2026, 11, 23)

    def test_next_midnight(self):
        assert next_pacific_midnight(T0) == datetime(2026, 11, 23, 8, 0, tzinfo=UTC)

    def test_naive_datetime_rejected(self):
        with pytest.raises(ValueError):
            pacific_date(datetime(2026, 1, 1))


class TestUsage:
    def test_record_returns_new_object(self):
        usage = new_usage(T0)
        updated = record(usage, T0, tokens=1200)
        assert usage.day_requests == 0 and usage.minute_events == ()
        assert updated.day_requests == 1
        assert updated.minute_events == ((T0, 1200),)

    def test_roll_prunes_old_minute_events(self):
        usage = record(record(new_usage(T0), T0, 10), T0 + timedelta(seconds=30), 20)
        rolled = roll(usage, T0 + timedelta(seconds=61))
        assert rolled.minute_events == ((T0 + timedelta(seconds=30), 20),)

    def test_roll_resets_day_after_pacific_midnight(self):
        usage = mark_exhausted(record(new_usage(T0), T0, 5), next_pacific_midnight(T0))
        rolled = roll(usage, T0 + timedelta(hours=1))
        assert rolled.day_requests == 0
        assert rolled.exhausted_until is None
        assert rolled.pt_date == date(2026, 11, 23)


class TestAvailability:
    def test_unknown_limits_are_ready(self):
        result = availability(new_usage(T0), ModelLimits(), T0)
        assert result.status is Status.READY

    def test_daily_limit_reached(self):
        usage = record(record(new_usage(T0), T0, 1), T0, 1)
        result = availability(usage, ModelLimits(rpd=2), T0 + timedelta(seconds=90))
        assert result.status is Status.UNAVAILABLE
        assert result.ready_at == next_pacific_midnight(T0)

    def test_minute_requests_limit_waits_for_oldest(self):
        usage = record(new_usage(T0), T0, 1)
        result = availability(usage, ModelLimits(rpm=1), T0 + timedelta(seconds=10))
        assert result.status is Status.WAIT
        assert result.ready_at == T0 + timedelta(seconds=60)

    def test_minute_tokens_limit(self):
        usage = record(record(new_usage(T0), T0, 30_000), T0 + timedelta(seconds=20), 10_000)
        now = T0 + timedelta(seconds=30)
        result = availability(usage, ModelLimits(tpm=50_000), now, est_tokens=15_000)
        assert result.status is Status.WAIT
        assert result.ready_at == T0 + timedelta(seconds=60)

    def test_tokens_fit(self):
        usage = record(new_usage(T0), T0, 30_000)
        assert availability(usage, ModelLimits(tpm=50_000), T0, est_tokens=10_000).status is Status.READY

    def test_exhausted_and_cooldown(self):
        until = T0 + timedelta(minutes=10)
        for usage in (mark_exhausted(new_usage(T0), until), mark_cooldown(new_usage(T0), until)):
            result = availability(usage, ModelLimits(), T0)
            assert result.status is Status.UNAVAILABLE
            assert result.ready_at == until

    def test_overload_cooldown_escalates_and_caps(self):
        initial, maximum = timedelta(seconds=60), timedelta(minutes=10)
        usage = new_usage(T0)
        expected = [60, 120, 240, 480, 600, 600]
        for seconds in expected:
            usage = mark_overloaded(usage, T0, initial, maximum)
            assert usage.cooldown_until == T0 + timedelta(seconds=seconds)
        assert usage.overload_streak == len(expected)

    def test_success_resets_overload_streak(self):
        usage = mark_overloaded(new_usage(T0), T0, timedelta(seconds=60), timedelta(minutes=10))
        assert usage.overload_streak == 1
        assert record(usage, T0 + timedelta(minutes=2), 1).overload_streak == 0

    def test_new_day_resets_overload_streak(self):
        usage = mark_overloaded(new_usage(T0), T0, timedelta(seconds=60), timedelta(minutes=10))
        assert roll(usage, T0 + timedelta(hours=2)).overload_streak == 0

    def test_cooldown_expires(self):
        usage = mark_cooldown(new_usage(T0), T0 + timedelta(minutes=10))
        assert availability(usage, ModelLimits(), T0 + timedelta(minutes=11)).status is Status.READY
