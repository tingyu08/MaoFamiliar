"""FastAPI 應用程式組裝：只綁本機、驗證 Host／token、提供 REST、WebSocket 與前端靜態檔。"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Callable

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.trustedhost import TrustedHostMiddleware

from familiar.api import routes, skill_routes, ws
from familiar.api.context import ServerSettings, Services
from familiar.api.envelope import http_error_handler, validation_error_handler


def create_app(services: Services, settings: ServerSettings, frontend_dir: Path | None = None,
               on_shutdown: Callable[[], None] | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        services.runner.recover()
        yield
        await services.runner.shutdown()
        if on_shutdown:
            on_shutdown()

    app = FastAPI(title="Familiar", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.services = services
    app.state.settings = settings
    # 防 DNS rebinding：只接受本機 Host
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(settings.allowed_hosts))
    app.add_middleware(CORSMiddleware, allow_origins=list(settings.extra_origins),
                       allow_methods=["GET", "POST"], allow_headers=["Authorization", "Content-Type"])
    app.add_exception_handler(StarletteHTTPException, http_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.include_router(routes.router)
    app.include_router(skill_routes.router)
    app.include_router(ws.router)
    if frontend_dir and frontend_dir.is_dir():
        app.mount("/", StaticFiles(directory=frontend_dir, html=True), name="frontend")
    return app
