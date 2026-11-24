"""API 需要的服務物件與伺服器設定。"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from typing import Callable

from familiar.core.events import EventBus
from familiar.core.task_runner import TaskRunner
from familiar.gemini.model_router import ModelRouter
from familiar.storage.artifact_store import ArtifactStore
from familiar.storage.task_repository import TaskRepository

DEV_ORIGINS = ("http://localhost:5173", "http://127.0.0.1:5173")  # Vite 開發伺服器


@dataclass(frozen=True)
class Services:
    runner: TaskRunner
    repo: TaskRepository
    artifacts: ArtifactStore
    router: ModelRouter
    events: EventBus
    close: Callable[[], None] = lambda: None  # 關閉資料庫等資源


@dataclass(frozen=True)
class ServerSettings:
    port: int = 8765
    token: str = field(default_factory=lambda: secrets.token_urlsafe(32))
    allowed_hosts: tuple[str, ...] = ("127.0.0.1", "localhost")
    extra_origins: tuple[str, ...] = DEV_ORIGINS

    @property
    def allowed_origins(self) -> tuple[str, ...]:
        own = tuple(f"http://{host}:{self.port}" for host in self.allowed_hosts)
        return (*own, *self.extra_origins)
