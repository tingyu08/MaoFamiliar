"""啟動 Familiar 後端：讀設定、檢查 API key、組裝所有元件、只在 127.0.0.1 提供服務。

用法：uv run python -m familiar.main
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv

from familiar.api.app import create_app
from familiar.api.context import ServerSettings, Services
from familiar.backends.base import ActionBackend
from familiar.backends.custom_fc import CustomFcBackend
from familiar.backends.native_cu import NativeCuBackend
from familiar.config import (AppConfig, load_config, to_actor_config, to_completion_config, to_reflex_config,
                             to_router_config, to_safety_config)
from familiar.core.agent_loop import AgentLoop
from familiar.core.agent_types import LoopConfig, LoopDeps
from familiar.core.events import EventBus
from familiar.core.models import Environment
from familiar.core.task_runner import TaskRunner
from familiar.executors.browser import BrowserExecutor
from familiar.gemini.interactions import AgentModelClient, GeminiInteractionsClient
from familiar.gemini.model_router import ModelRouter
from familiar.gemini.quota import pacific_date
from familiar.gemini.decisions import DecisionClient, GeminiDecisionClient
from familiar.knowledge.completion_check import CompletionChecker
from familiar.knowledge.decision_actor import DecisionActor
from familiar.knowledge.reflex import ReflexDecider
from familiar.knowledge.replay import ReplayContext
from familiar.knowledge.skill_store import SkillStore
from familiar.storage.artifact_store import ArtifactStore
from familiar.storage.db import Database
from familiar.storage.quota_repository import QuotaRepository
from familiar.storage.task_repository import TaskRepository

BACKEND_DIR = Path(__file__).resolve().parents[1]
logger = logging.getLogger("familiar")


def utc_now() -> datetime:
    return datetime.now(UTC)


def require_api_key() -> str:
    load_dotenv(BACKEND_DIR / ".env")
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        sys.exit("找不到 GEMINI_API_KEY：請把 backend/.env.example 複製成 backend/.env 並填入免費版 API key。")
    return key


def make_backend(config: AppConfig) -> ActionBackend:
    return NativeCuBackend() if config.gemini.backend == "native_cu" else CustomFcBackend()


def build_services(config: AppConfig, data_dir: Path, client: AgentModelClient, headless: bool = False,
                   decisions: DecisionClient | None = None, loop_config: LoopConfig | None = None) -> Services:
    db = Database(data_dir / "familiar.db")
    repo = TaskRepository(db)
    quota_repo = QuotaRepository(db)
    artifacts = ArtifactStore(data_dir / "screenshots")
    events = EventBus()
    router = ModelRouter(to_router_config(config), clock=utc_now, sleep=asyncio.sleep,
                         initial_usage=quota_repo.load(pacific_date(utc_now())), on_change=quota_repo.save)
    backend = make_backend(config)
    safety = to_safety_config(config)
    profile_dir = data_dir / "browser-profile"

    def executor_factory(environment: Environment) -> BrowserExecutor:
        if environment is not Environment.BROWSER:
            raise ValueError("目前只支援瀏覽器（桌面執行器在 P2）")
        return BrowserExecutor(profile_dir=profile_dir, headless=headless)

    actor = DecisionActor(router, decisions, to_actor_config(config)) if decisions else None
    completion = CompletionChecker(router, decisions, to_completion_config(config)) if decisions else None
    reflex = ReflexDecider(router, decisions, to_reflex_config(config)) if decisions else None

    def loop_factory(executor, control, on_step, on_status) -> AgentLoop:
        deps = LoopDeps(router=router, client=client, backend=backend, executor=executor, events=events,
                        control=control, safety=safety, clock=utc_now, on_step=on_step, on_status=on_status,
                        actor=actor, completion=completion, reflex=reflex)
        return AgentLoop(deps, loop_config)

    skills = SkillStore(data_dir / "targets")
    runner = TaskRunner(repo, artifacts, events, executor_factory, loop_factory, clock=utc_now,
                        replay=ReplayContext(skills, repo, utc_now))
    return Services(runner=runner, repo=repo, artifacts=artifacts, router=router, events=events, close=db.close,
                    skills=skills)


def main() -> None:
    import uvicorn
    from google import genai

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s：%(message)s")
    key = require_api_key()
    config = load_config(BACKEND_DIR / "config.yaml")
    http_options = {"timeout": 90_000, "retry_options": {"attempts": 1}}
    sdk = genai.Client(api_key=key, http_options=http_options)
    decisions_sdk = genai.Client(api_key=key, http_options={**http_options, "api_version": "v1beta"})
    services = build_services(config, BACKEND_DIR / "data", GeminiInteractionsClient(sdk),
                              decisions=GeminiDecisionClient(decisions_sdk))
    settings = ServerSettings(port=8765)
    app = create_app(services, settings, frontend_dir=BACKEND_DIR.parent / "frontend" / "dist",
                     on_shutdown=services.close)
    print(f"\nFamiliar 已啟動：http://127.0.0.1:{settings.port}/?token={settings.token}\n"
          "（token 每次啟動都會重新產生，只在這台電腦上有效）\n", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=settings.port, log_level="warning")


if __name__ == "__main__":
    main()
