"""技能 API：從成功的任務存成技能、列出、查看、重播、刪除（SDD 5.4）。"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from familiar.api.context import Services
from familiar.api.envelope import ok
from familiar.api.routes import require_token, services
from familiar.core.task_runner import InvalidTaskError, TaskBusyError
from familiar.knowledge.skill_store import SkillFormatError, SkillNotFound, SkillStore
from familiar.knowledge.skills import SkillBuildError, SkillMeta, build_steps


class CreateSkillRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task_id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=60)


router = APIRouter(prefix="/api/skills", dependencies=[Depends(require_token)])
Svc = Annotated[Services, Depends(services)]


def _store(svc: Services) -> SkillStore:
    if svc.skills is None:
        raise HTTPException(status_code=404, detail="沒有啟用技能功能")
    return svc.skills


def view(meta: SkillMeta, step_count: int | None = None) -> dict[str, Any]:
    data = {**meta.model_dump(mode="json"), "needs_relearn": meta.needs_relearn}
    return data if step_count is None else {**data, "step_count": step_count}


def _load(store: SkillStore, skill_id: str):
    try:
        return store.meta(skill_id), store.version(skill_id)
    except SkillNotFound as error:
        raise HTTPException(status_code=404, detail="找不到這個技能") from error
    except SkillFormatError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.post("")
async def create_skill(body: CreateSkillRequest, svc: Svc):
    store = _store(svc)
    task = svc.repo.get(body.task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="找不到這個任務")
    try:
        steps = build_steps(task, svc.repo.steps(task.id))
        meta = store.create(body.name, task.instruction, task.target, steps, task.id, task.created_at)
    except (SkillBuildError, ValueError) as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return ok(view(meta, len(steps)), status_code=201)


@router.get("")
async def list_skills(svc: Svc):
    return ok([view(meta) for meta in _store(svc).list()])


@router.get("/{skill_id}")
async def get_skill(skill_id: str, svc: Svc):
    meta, version = _load(_store(svc), skill_id)
    return ok({"skill": view(meta, len(version.steps)), "steps": version.model_dump(mode="json")["steps"]})


@router.post("/{skill_id}/run")
async def run_skill(skill_id: str, svc: Svc):
    _load(_store(svc), skill_id)  # 先確認存在且格式正確
    try:
        record = await svc.runner.start_replay(skill_id)
    except TaskBusyError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except InvalidTaskError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return ok(record, status_code=201)


@router.delete("/{skill_id}")
async def delete_skill(skill_id: str, svc: Svc):
    store = _store(svc)
    try:
        store.delete(skill_id)
    except SkillNotFound as error:
        raise HTTPException(status_code=404, detail="找不到這個技能") from error
    return ok({"deleted": skill_id})
