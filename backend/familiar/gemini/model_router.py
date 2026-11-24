"""依工作分工挑模型、節流、重試、退階（SRS FR-201～209、SDD 4.4）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any, Awaitable, Callable, Generic, Mapping, TypeVar

from familiar.gemini.errors import ErrorKind, classify
from familiar.gemini.quota import (
    ModelLimits,
    ModelUsage,
    Status,
    availability,
    mark_cooldown,
    mark_exhausted,
    mark_overloaded,
    new_usage,
    next_pacific_midnight,
    record,
)

T = TypeVar("T")
DEFAULT_MINUTE_WAIT = timedelta(seconds=60)


class Role(StrEnum):
    ACTION = "action"      # 每一步的操作
    PLANNING = "planning"  # 拆子目標、反思、整理心得
    DECISION = "decision"  # Decisions API


@dataclass(frozen=True)
class RouterConfig:
    chains: Mapping[Role, tuple[str, ...]]
    limits: Mapping[str, ModelLimits] = field(default_factory=dict)
    retries: int = 3                          # 鏈上沒有其他可用模型時，過載重試次數
    backoff_sec: tuple[float, ...] = (10.0, 20.0, 40.0)
    fallback_retries: int = 1                 # 還有其他可用模型時，只重試這麼多次就換模型
    fallback_backoff_sec: float = 5.0
    cooldown_initial: timedelta = timedelta(seconds=60)  # 過載冷卻從這裡起跳，連續過載加倍
    cooldown_max: timedelta = timedelta(minutes=10)
    max_wait_sec: float = 120.0  # 最早可用時間在這之內就等，否則交給呼叫端（例如轉為 waiting_quota）
    planning_falls_back_to_action: bool = True

    def __post_init__(self) -> None:
        for role, chain in self.chains.items():
            if not chain:
                raise ValueError(f"{role} 的模型鏈不能是空的")
        if self.retries < 0 or self.fallback_retries < 0 or not self.backoff_sec:
            raise ValueError("重試次數不能為負數，backoff_sec 不能是空的")
        if not timedelta(0) < self.cooldown_initial <= self.cooldown_max:
            raise ValueError("冷卻時間需滿足 0 < cooldown_initial ≤ cooldown_max")


class NoModelAvailable(Exception):
    def __init__(self, role: Role, ready_at: datetime | None):
        super().__init__(f"{role} 目前沒有可用的模型，預計恢復時間：{ready_at}")
        self.role = role
        self.ready_at = ready_at


@dataclass(frozen=True)
class RoutedResult(Generic[T]):
    value: T
    model: str
    attempts: int


Clock = Callable[[], datetime]
Sleep = Callable[[float], Awaitable[None]]


UsageListener = Callable[[str, ModelUsage], None]


class ModelRouter:
    def __init__(
        self,
        config: RouterConfig,
        clock: Clock,
        sleep: Sleep,
        initial_usage: Mapping[str, ModelUsage] | None = None,
        on_change: UsageListener | None = None,
    ):
        self._config = config
        self._clock = clock
        self._sleep = sleep
        self._usage: Mapping[str, ModelUsage] = dict(initial_usage or {})
        self._on_change = on_change

    # ---- 查詢 ----

    def candidates(self, role: Role) -> tuple[str, ...]:
        chain = self._config.chains.get(role)
        if chain is None:
            raise ValueError(f"沒有設定 {role} 的模型鏈")
        if role is Role.PLANNING and self._config.planning_falls_back_to_action:
            chain = (*chain, *self._config.chains.get(Role.ACTION, ()))
        return tuple(dict.fromkeys(chain))

    def snapshot(self) -> dict[str, dict[str, Any]]:
        """給前端顯示各模型今日用量（FR-207）。"""
        now = self._clock()
        models = dict.fromkeys(m for chain in self._config.chains.values() for m in chain)
        report = {}
        for model in models:
            limits = self._limits(model)
            usage = self._usage_of(model, now)
            state = availability(usage, limits, now)
            report[model] = {"day_requests": usage.day_requests, "rpd": limits.rpd, "rpm": limits.rpm,
                             "tpm": limits.tpm, "status": state.status.value, "ready_at": state.ready_at}
        return report

    # ---- 呼叫 ----

    async def call(
        self,
        role: Role,
        fn: Callable[[str], Awaitable[T]],
        est_tokens: int = 0,
        tokens_of: Callable[[T], int] | None = None,
    ) -> RoutedResult[T]:
        max_hops = len(self.candidates(role)) * 4 + 10
        for _ in range(max_hops):
            model, wait = self._pick(role, est_tokens)
            if wait > 0:
                await self._sleep(wait)
                continue
            result = await self._attempt(model, fn, tokens_of, self._has_backup(role, model, est_tokens))
            if result is not None:
                return result
        raise NoModelAvailable(role, None)

    # ---- 內部 ----

    def _limits(self, model: str) -> ModelLimits:
        return self._config.limits.get(model, ModelLimits())

    def _usage_of(self, model: str, now: datetime) -> ModelUsage:
        return self._usage.get(model) or new_usage(now)

    def _set_usage(self, model: str, usage: ModelUsage) -> None:
        self._usage = {**self._usage, model: usage}
        if self._on_change:
            self._on_change(model, usage)

    def _pick(self, role: Role, est_tokens: int) -> tuple[str, float]:
        now = self._clock()
        states = [(m, availability(self._usage_of(m, now), self._limits(m), now, est_tokens))
                  for m in self.candidates(role)]
        for model, state in states:
            if state.status is Status.READY:
                return model, 0.0
        timed = [(state.ready_at, state.status, model) for model, state in states if state.ready_at]
        waits = [t for t in timed if t[1] is Status.WAIT]
        ready_at, _, model = min(waits or timed, default=(None, None, None))
        if ready_at is None or model is None:
            raise NoModelAvailable(role, None)
        seconds = max(0.0, (ready_at - now).total_seconds())
        if waits or seconds <= self._config.max_wait_sec:
            return model, seconds
        raise NoModelAvailable(role, ready_at)

    def _has_backup(self, role: Role, model: str, est_tokens: int) -> bool:
        """鏈上除了這個模型，是否還有沒被停用的模型（可以立即用或稍等就能用）。"""
        now = self._clock()
        return any(
            availability(self._usage_of(m, now), self._limits(m), now, est_tokens).status is not Status.UNAVAILABLE
            for m in self.candidates(role)
            if m != model
        )

    def _backoff(self, attempt: int, has_backup: bool) -> float:
        if has_backup:
            return self._config.fallback_backoff_sec
        delays = self._config.backoff_sec
        return delays[min(attempt - 1, len(delays) - 1)]

    async def _attempt(
        self, model: str, fn: Callable[[str], Awaitable[T]], tokens_of: Callable[[T], int] | None,
        has_backup: bool,
    ) -> RoutedResult[T] | None:
        """在同一個模型上呼叫（含過載重試）。成功回傳結果；需要換模型時回傳 None；其他錯誤往上拋。

        還有備援模型時只重試 fallback_retries 次就換模型（避免空等）；沒有備援時才用完整的退避重試。
        """
        retries = self._config.fallback_retries if has_backup else self._config.retries
        for attempt in range(1, retries + 2):
            try:
                value = await fn(model)
            except Exception as exc:  # noqa: BLE001 — 依種類分流，無法處理的會重新拋出
                error = classify(exc)
                if error.kind in (ErrorKind.OVERLOADED, ErrorKind.TIMEOUT) and attempt <= retries:
                    await self._sleep(self._backoff(attempt, has_backup))
                    continue
                if not self._handle_failure(model, error.kind, error.retry_after):
                    raise
                return None
            now = self._clock()
            tokens = tokens_of(value) if tokens_of else 0
            self._set_usage(model, record(self._usage_of(model, now), now, tokens))
            return RoutedResult(value, model, attempt)
        return None  # pragma: no cover — 迴圈一定會 return 或 raise

    def _handle_failure(self, model: str, kind: ErrorKind, retry_after: timedelta | None) -> bool:
        """更新模型狀態。回傳 False 表示這種錯誤不該換模型，應交給呼叫端。"""
        now = self._clock()
        usage = self._usage_of(model, now)
        if kind is ErrorKind.DAILY_QUOTA:
            until = now + retry_after if retry_after else next_pacific_midnight(now)
            self._set_usage(model, mark_exhausted(usage, until))
        elif kind is ErrorKind.MINUTE_QUOTA:
            self._set_usage(model, mark_cooldown(usage, now + (retry_after or DEFAULT_MINUTE_WAIT)))
        elif kind in (ErrorKind.OVERLOADED, ErrorKind.TIMEOUT):
            self._set_usage(model, mark_overloaded(usage, now, self._config.cooldown_initial,
                                                   self._config.cooldown_max))
        else:
            return False
        return True
