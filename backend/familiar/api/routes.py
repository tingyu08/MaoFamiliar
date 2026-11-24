"""REST API（SDD 5.4）。所有端點都要帶 session token。"""

from __future__ import annotations

import secrets
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from familiar.api.context import ServerSettings, Services
from familiar.api.envelope import ok
from familiar.core.task_runner import InvalidTaskError, TaskBusyError


class CreateTaskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str = Field(min_length=1, max_length=2048)
    instruction: str = Field(min_length=1, max_length=2000)


def services(request: Request) -> Services:
    return request.app.state.services


def settings(request: Request) -> ServerSettings:
    return request.app.state.settings


def require_token(request: Request, token: Annotated[str | None, Query()] = None) -> None:
    """Authorization: Bearer <token>；<img> 這類無法帶標頭的請求可改用 ?token=。"""
    expected = settings(request).token
    header = request.headers.get("authorization", "")
    supplied = header[7:] if header.lower().startswith("bearer ") else (token or "")
    if not supplied or not secrets.compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail="未授權：缺少或錯誤的 token")


router = APIRouter(prefix="/api", dependencies=[Depends(require_token)])
Svc = Annotated[Services, Depends(services)]


@router.post("/tasks")
async def create_task(body: CreateTaskRequest, svc: Svc):
    try:
        record = await svc.runner.start(body.url, body.instruction)
    except InvalidTaskError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except TaskBusyError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return ok(record, status_code=201)


@router.get("/tasks")
async def list_tasks(svc: Svc, limit: Annotated[int, Query(ge=1, le=100)] = 20,
                     offset: Annotated[int, Query(ge=0)] = 0):
    items, total = svc.repo.list(limit=limit, offset=offset)
    return ok(items, meta={"total": total, "limit": limit, "offset": offset})


@router.get("/tasks/{task_id}")
async def get_task(task_id: str, svc: Svc):
    task = svc.repo.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="找不到這個任務")
    return ok({"task": task, "steps": svc.repo.steps(task_id), "running": svc.runner.current_task_id == task_id})


@router.post("/tasks/{task_id}/stop")
async def stop_task(task_id: str, svc: Svc):
    if not svc.runner.stop(task_id):
        raise HTTPException(status_code=409, detail="這個任務目前沒有在執行")
    return ok({"stopping": True})


@router.get("/tasks/{task_id}/steps/{idx}/screenshot")
async def step_screenshot(task_id: str, idx: int, svc: Svc):
    step = svc.repo.step(task_id, idx)
    if step is None or not step.screenshot_path:
        raise HTTPException(status_code=404, detail="找不到這一步的截圖")
    try:
        data = svc.artifacts.read(step.screenshot_path)
    except (FileNotFoundError, ValueError) as error:
        raise HTTPException(status_code=404, detail="截圖檔案不存在") from error
    return Response(content=data, media_type="image/png")


@router.get("/quota")
async def quota(svc: Svc):
    return ok(svc.router.snapshot())
