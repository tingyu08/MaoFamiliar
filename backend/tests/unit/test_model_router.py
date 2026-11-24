from datetime import UTC, datetime, timedelta

import pytest

from familiar.gemini.model_router import ModelRouter, NoModelAvailable, Role, RouterConfig
from familiar.gemini.quota import ModelLimits, next_pacific_midnight

T0 = datetime(2026, 11, 23, 7, 0, tzinfo=UTC)


class ApiError(Exception):
    def __init__(self, status_code: int, message: str = ""):
        super().__init__(message)
        self.status_code = status_code


DAILY_429 = ApiError(429, "limit: 20 requests per day on Free Tier. Please retry in 59m0s")
OVERLOADED = ApiError(503, "high demand")
SAFETY = ApiError(400, "Request blocked due to safety violations")


class FakeClock:
    def __init__(self, start: datetime):
        self.now = start
        self.sleeps: list[float] = []

    def __call__(self) -> datetime:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += timedelta(seconds=seconds)


class ScriptedApi:
    """依模型回傳預先排好的結果（例外或值），並記錄呼叫順序。"""

    def __init__(self, script: dict[str, list]):
        self.script = {model: list(results) for model, results in script.items()}
        self.calls: list[str] = []

    async def __call__(self, model: str):
        self.calls.append(model)
        result = self.script[model].pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def make_router(clock, limits=None, action=("lite", "flash"), planning=("hydrogen", "flash")):
    config = RouterConfig(
        chains={Role.ACTION: action, Role.PLANNING: planning, Role.DECISION: ("decision",)},
        limits=limits or {},
        retries=3,
        backoff_sec=(10.0, 20.0, 40.0),
        fallback_retries=1,
        fallback_backoff_sec=5.0,
        cooldown_initial=timedelta(seconds=60),
        cooldown_max=timedelta(minutes=10),
    )
    return ModelRouter(config, clock=clock, sleep=clock.sleep)


@pytest.fixture
def clock():
    return FakeClock(T0)


async def test_uses_first_model(clock):
    router = make_router(clock)
    api = ScriptedApi({"lite": ["ok"]})
    result = await router.call(Role.ACTION, api)
    assert (result.value, result.model, result.attempts) == ("ok", "lite", 1)


async def test_daily_quota_falls_back_and_remembers(clock):
    router = make_router(clock)
    api = ScriptedApi({"lite": [DAILY_429], "flash": ["a", "b"]})
    first = await router.call(Role.ACTION, api)
    second = await router.call(Role.ACTION, api)
    assert first.model == "flash" and second.model == "flash"
    assert api.calls == ["lite", "flash", "flash"]


async def test_overloaded_with_backup_retries_once_then_falls_back(clock):
    router = make_router(clock)
    api = ScriptedApi({"lite": [OVERLOADED] * 2, "flash": ["ok"]})
    result = await router.call(Role.ACTION, api)
    assert result.model == "flash"
    assert api.calls == ["lite", "lite", "flash"]
    assert clock.sleeps == [5.0]


async def test_overloaded_last_model_retries_with_full_backoff(clock):
    router = make_router(clock, action=("lite",))
    api = ScriptedApi({"lite": [OVERLOADED] * 3 + ["ok"]})
    result = await router.call(Role.ACTION, api)
    assert (result.model, result.attempts) == ("lite", 4)
    assert clock.sleeps == [10.0, 20.0, 40.0]


async def test_backup_on_cooldown_counts_as_no_backup(clock):
    router = make_router(clock)
    first = await router.call(Role.ACTION, ScriptedApi({"lite": [DAILY_429], "flash": ["x"]}))
    assert first.model == "flash"
    # lite 今日已停用，flash 是唯一可用的模型 → 用完整重試
    result = await router.call(Role.ACTION, ScriptedApi({"flash": [OVERLOADED, OVERLOADED, "ok"]}))
    assert result.model == "flash" and result.attempts == 3
    assert clock.sleeps == [10.0, 20.0]


async def test_overload_cooldown_escalates_then_resets(clock):
    router = make_router(clock)
    overloaded_twice = {"lite": [OVERLOADED, OVERLOADED], "flash": ["ok"]}

    await router.call(Role.ACTION, ScriptedApi(overloaded_twice))
    assert router.snapshot()["lite"]["ready_at"] == clock.now + timedelta(seconds=60)

    clock.now += timedelta(seconds=61)
    await router.call(Role.ACTION, ScriptedApi(overloaded_twice))
    assert router.snapshot()["lite"]["ready_at"] == clock.now + timedelta(seconds=120)

    clock.now += timedelta(seconds=121)
    assert (await router.call(Role.ACTION, ScriptedApi({"lite": ["ok"]}))).model == "lite"
    await router.call(Role.ACTION, ScriptedApi(overloaded_twice))
    assert router.snapshot()["lite"]["ready_at"] == clock.now + timedelta(seconds=60)


async def test_all_models_cooling_waits_short_time(clock):
    router = make_router(clock)
    api = ScriptedApi({"lite": [OVERLOADED, OVERLOADED, "ok"], "flash": [OVERLOADED] * 4})
    result = await router.call(Role.ACTION, api)
    assert result.model == "lite"
    # lite 冷卻 60 秒；flash 成為唯一可用模型時完整重試後也冷卻 → 只需等 lite 的 60 秒
    assert sum(clock.sleeps) < 5 + 10 + 20 + 40 + 61


async def test_overloaded_then_success_on_same_model(clock):
    router = make_router(clock)
    api = ScriptedApi({"lite": [OVERLOADED, "ok"]})
    result = await router.call(Role.ACTION, api)
    assert (result.model, result.attempts) == ("lite", 2)
    assert clock.sleeps == [5.0]


async def test_cooldown_expires_and_earlier_model_returns(clock):
    router = make_router(clock)
    api = ScriptedApi({"lite": [OVERLOADED] * 2 + ["back"], "flash": ["ok"]})
    await router.call(Role.ACTION, api)
    clock.now += timedelta(minutes=11)
    result = await router.call(Role.ACTION, api)
    assert result.model == "lite" and result.value == "back"


async def test_safety_block_is_raised_without_fallback(clock):
    router = make_router(clock)
    api = ScriptedApi({"lite": [SAFETY], "flash": ["unused"]})
    with pytest.raises(ApiError):
        await router.call(Role.ACTION, api)
    assert api.calls == ["lite"]


async def test_all_exhausted_raises_with_ready_time(clock):
    router = make_router(clock, action=("lite",), planning=("hydrogen",))
    api = ScriptedApi({"lite": [ApiError(429, "20 requests per day")]})
    with pytest.raises(NoModelAvailable) as info:
        await router.call(Role.ACTION, api)
    assert info.value.ready_at == next_pacific_midnight(T0)


async def test_planning_falls_back_to_action_chain(clock):
    router = make_router(clock, planning=("hydrogen",))
    api = ScriptedApi({"hydrogen": [DAILY_429], "lite": ["planned"]})
    result = await router.call(Role.PLANNING, api)
    assert result.model == "lite"


async def test_quota_is_shared_between_chains(clock):
    router = make_router(clock, limits={"flash": ModelLimits(rpd=1)}, action=("flash", "lite"),
                         planning=("flash", "hydrogen"))
    api = ScriptedApi({"flash": ["x"], "hydrogen": ["y"]})
    await router.call(Role.ACTION, api)
    result = await router.call(Role.PLANNING, api)
    assert result.model == "hydrogen"


async def test_rpm_pacing_waits(clock):
    router = make_router(clock, limits={"decision": ModelLimits(rpm=1)})
    api = ScriptedApi({"decision": ["a", "b"]})
    await router.call(Role.DECISION, api)
    await router.call(Role.DECISION, api)
    assert clock.sleeps == [60.0]


async def test_tokens_are_recorded_for_tpm(clock):
    router = make_router(clock, limits={"decision": ModelLimits(tpm=1000)})
    api = ScriptedApi({"decision": ["a", "b"]})
    await router.call(Role.DECISION, api, est_tokens=600, tokens_of=lambda _: 900)
    await router.call(Role.DECISION, api, est_tokens=600, tokens_of=lambda _: 900)
    assert clock.sleeps == [60.0]


async def test_minute_quota_error_sets_short_cooldown(clock):
    router = make_router(clock, action=("lite",))
    api = ScriptedApi({"lite": [ApiError(429, "per minute. Please retry in 20s"), "ok"]})
    result = await router.call(Role.ACTION, api)
    assert result.value == "ok"
    assert clock.sleeps == [20.0]


async def test_snapshot_reports_usage(clock):
    router = make_router(clock, limits={"lite": ModelLimits(rpd=20)})
    await router.call(Role.ACTION, ScriptedApi({"lite": ["ok"]}))
    snapshot = router.snapshot()
    assert snapshot["lite"]["day_requests"] == 1
    assert snapshot["lite"]["rpd"] == 20
    assert snapshot["lite"]["status"] == "ready"


async def test_persistence_hooks(clock):
    from familiar.gemini.quota import ModelUsage, pacific_date

    saved = {}
    preloaded = {"lite": ModelUsage(pt_date=pacific_date(T0), day_requests=19)}
    config = RouterConfig(chains={Role.ACTION: ("lite", "flash")}, limits={"lite": ModelLimits(rpd=20)})
    router = ModelRouter(config, clock=clock, sleep=clock.sleep, initial_usage=preloaded,
                         on_change=lambda model, usage: saved.update({model: usage}))
    api = ScriptedApi({"lite": ["x"], "flash": ["y"]})
    assert (await router.call(Role.ACTION, api)).model == "lite"
    assert saved["lite"].day_requests == 20
    assert (await router.call(Role.ACTION, api)).model == "flash"


def test_config_rejects_empty_chain():
    with pytest.raises(ValueError):
        RouterConfig(chains={Role.ACTION: ()}, limits={})


def test_config_rejects_negative_fallback_retries():
    with pytest.raises(ValueError):
        RouterConfig(chains={Role.ACTION: ("lite",)}, fallback_retries=-1)
