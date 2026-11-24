"""讀取並驗證 config.yaml（SDD 第 9 節）。目前只涵蓋第 1 週用到的區塊，其餘區塊先忽略。"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from familiar.core.safety import SafetyConfig
from familiar.gemini.model_router import Role, RouterConfig
from familiar.gemini.quota import ModelLimits
from familiar.knowledge.completion_check import CompletionConfig
from familiar.backends.action_args import MAX_REFLEX_ITERATIONS
from familiar.knowledge.decision_actor import ActorConfig
from familiar.knowledge.reflex import ReflexConfig


class _Strict(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class LimitsCfg(_Strict):
    rpm: int = Field(default=0, ge=0)
    rpd: int = Field(default=0, ge=0)
    tpm: int = Field(default=0, ge=0)


class ChainsCfg(_Strict):
    action: tuple[str, ...] = Field(min_length=1)
    planning: tuple[str, ...] = Field(min_length=1)


class OverloadCfg(_Strict):
    retries: int = Field(default=3, ge=0, le=10)
    backoff_sec: tuple[float, ...] = Field(default=(10.0, 20.0, 40.0), min_length=1)
    fallback_retries: int = Field(default=1, ge=0, le=10)
    fallback_backoff_sec: float = Field(default=5.0, ge=0, le=120)
    cooldown_initial_sec: float = Field(default=60, gt=0)
    cooldown_max_min: float = Field(default=10, gt=0)


class DecisionCfg(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")  # actor 等欄位之後的週次再驗證
    model: str = "gemini-decision-1"
    rpm: int = Field(default=200, ge=0)
    rpd: int = Field(default=10_000, ge=0)
    tpm: int = Field(default=50_000, ge=0)
    screenshot_max_side: int = Field(default=1024, ge=256, le=4096)
    confidence_threshold: float = Field(default=0.8, gt=0, le=1)


class ActorCfg(_Strict):
    """第 3 層：Decisions 選候選（SDD 2.1）。"""
    enabled: bool = True
    mark_threshold: float = Field(default=0.8, gt=0, le=1)
    risk_threshold: float = Field(default=0.5, gt=0, le=1)
    max_candidates: int = Field(default=60, ge=1, le=254)
    stall_steps: int = Field(default=2, ge=1)


class CompletionCfg(_Strict):
    """完成檢查（FR-605）：模型回報完成前先看畫面確認。"""
    enabled: bool = True
    threshold: float = Field(default=0.5, gt=0, le=1)
    max_rejections: int = Field(default=2, ge=0, le=10)


class ReflexCfg(_Strict):
    """反射層：遊戲模式下 Decisions 看畫面、照代理人定的規則按鍵（FR-670～676）。"""
    enabled: bool = True
    max_side: int = Field(default=512, ge=128, le=2048)
    threshold: float = Field(default=0.5, gt=0, le=1)
    max_unknown: int = Field(default=3, ge=1, le=20)
    stall_ticks: int = Field(default=6, ge=2, le=60)
    max_iterations: int = Field(default=60, ge=1, le=MAX_REFLEX_ITERATIONS)


class GeminiCfg(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")
    models: dict[str, LimitsCfg] = Field(min_length=1)
    backend: Literal["custom_fc", "native_cu"] = "custom_fc"
    chains: ChainsCfg
    overload: OverloadCfg = OverloadCfg()
    decision: DecisionCfg = DecisionCfg()
    actor: ActorCfg = ActorCfg()
    completion_check: CompletionCfg = CompletionCfg()
    reflex: ReflexCfg = ReflexCfg()
    enable_prompt_injection_detection: bool = True

    @model_validator(mode="after")
    def _chains_use_known_models(self) -> "GeminiCfg":
        unknown = [m for m in (*self.chains.action, *self.chains.planning) if m not in self.models]
        if unknown:
            raise ValueError(f"模型鏈中的模型沒有在 gemini.models 設定配額：{', '.join(unknown)}")
        return self


class BrowserCfg(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")
    blocked_domains: tuple[str, ...] = ()


class DesktopCfg(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")
    blocked_windows: tuple[str, ...] = ()


class AppConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")
    gemini: GeminiCfg
    browser: BrowserCfg = BrowserCfg()
    desktop: DesktopCfg = DesktopCfg()


def load_config(path: Path) -> AppConfig:
    if not path.is_file():
        raise FileNotFoundError(f"找不到設定檔 config：{path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return AppConfig.model_validate(data)


def to_router_config(config: AppConfig) -> RouterConfig:
    g = config.gemini
    limits = {name: ModelLimits(c.rpm, c.rpd, c.tpm) for name, c in g.models.items()}
    limits = {**limits, g.decision.model: ModelLimits(g.decision.rpm, g.decision.rpd, g.decision.tpm)}
    return RouterConfig(
        chains={Role.ACTION: g.chains.action, Role.PLANNING: g.chains.planning, Role.DECISION: (g.decision.model,)},
        limits=limits,
        retries=g.overload.retries,
        backoff_sec=tuple(float(s) for s in g.overload.backoff_sec),
        fallback_retries=g.overload.fallback_retries,
        fallback_backoff_sec=g.overload.fallback_backoff_sec,
        cooldown_initial=timedelta(seconds=g.overload.cooldown_initial_sec),
        cooldown_max=timedelta(minutes=g.overload.cooldown_max_min),
    )


def to_actor_config(config: AppConfig) -> ActorConfig:
    a = config.gemini.actor
    return ActorConfig(enabled=a.enabled, mark_threshold=a.mark_threshold, risk_threshold=a.risk_threshold,
                       max_candidates=a.max_candidates, stall_steps=a.stall_steps,
                       screenshot_max_side=config.gemini.decision.screenshot_max_side)


def to_completion_config(config: AppConfig) -> CompletionConfig:
    c = config.gemini.completion_check
    return CompletionConfig(enabled=c.enabled, threshold=c.threshold, max_rejections=c.max_rejections,
                            screenshot_max_side=config.gemini.decision.screenshot_max_side)


def to_reflex_config(config: AppConfig) -> ReflexConfig:
    r = config.gemini.reflex
    return ReflexConfig(enabled=r.enabled, max_side=r.max_side, threshold=r.threshold, max_unknown=r.max_unknown,
                        stall_ticks=r.stall_ticks, max_iterations=r.max_iterations)


def to_safety_config(config: AppConfig) -> SafetyConfig:
    return SafetyConfig(blocked_domains=config.browser.blocked_domains,
                        blocked_windows=config.desktop.blocked_windows)
