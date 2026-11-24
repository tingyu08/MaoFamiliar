"""本機安全規則：決定動作要直接執行、請使用者確認，還是擋下。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import urlparse

from familiar.core.models import (
    IMPACTFUL_KINDS,
    TEXT_INPUT_KINDS,
    Action,
    ActionKind,
    Environment,
    Risk,
)

ALLOWED_SCHEMES = frozenset({"http", "https"})


class Verdict(StrEnum):
    ALLOW = "allow"
    CONFIRM = "confirm"
    BLOCK = "block"


@dataclass(frozen=True)
class SafetyConfig:
    blocked_domains: tuple[str, ...] = ()
    blocked_windows: tuple[str, ...] = ()


@dataclass(frozen=True)
class SafetyContext:
    environment: Environment
    current_url: str | None = None
    window_title: str | None = None
    target_is_password: bool = False  # 由執行器在評估前判斷輸入目標


@dataclass(frozen=True)
class SafetyResult:
    verdict: Verdict
    reason: str = ""


ALLOW = SafetyResult(Verdict.ALLOW)


def _host_blocked(url: str, domains: tuple[str, ...]) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return any(host == d or host.endswith(f".{d}") for d in (d.lower() for d in domains))


def _window_blocked(title: str | None, patterns: tuple[str, ...]) -> bool:
    lowered = (title or "").lower()
    return bool(lowered) and any(p.lower() in lowered for p in patterns)


def _blocking_reason(action: Action, ctx: SafetyContext, cfg: SafetyConfig) -> str | None:
    if action.kind is ActionKind.NAVIGATE:
        url = str(action.args.get("url", ""))
        if urlparse(url).scheme not in ALLOWED_SCHEMES:
            return "invalid_url"
        if _host_blocked(url, cfg.blocked_domains):
            return "blocked_domain"
    if ctx.current_url and _host_blocked(ctx.current_url, cfg.blocked_domains):
        return "blocked_domain"
    if _window_blocked(ctx.window_title, cfg.blocked_windows):
        return "blocked_window"
    if action.kind in TEXT_INPUT_KINDS and ctx.target_is_password:
        return "password"
    return None


def evaluate(action: Action, ctx: SafetyContext, cfg: SafetyConfig) -> SafetyResult:
    """依序檢查：擋下 → 原生安全判定 → 模型自評高風險 → 允許。"""
    blocked = _blocking_reason(action, ctx, cfg)
    if blocked:
        return SafetyResult(Verdict.BLOCK, blocked)
    if action.safety and action.safety.requires_confirmation:
        return SafetyResult(Verdict.CONFIRM, action.safety.explanation or "model_safety")
    if action.kind in IMPACTFUL_KINDS and action.risk is Risk.HIGH:
        return SafetyResult(Verdict.CONFIRM, "high_risk")
    return ALLOW
