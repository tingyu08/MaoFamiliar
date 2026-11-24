"""技能檔案存取：data/targets/<target_id>/skills/<skill_id>/{meta.json, v<n>.json}。

每次修改都寫新版本、不覆寫舊版本（SDD 設計原則 4）；寫檔先寫暫存檔再換名，避免寫一半壞掉。
"""

from __future__ import annotations

import json
import re
import shutil
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Sequence

from pydantic import ValidationError

from familiar.knowledge.skills import SkillMeta, SkillStep, SkillVersion, target_id_for

_SAFE_ID = re.compile(r"^[a-f0-9]{12}$")


class SkillNotFound(KeyError):
    pass


class SkillFormatError(ValueError):
    """技能檔格式不相容或損壞（FR：schema_version 不符時拒絕載入）。"""


REPLACE_ATTEMPTS = 6
REPLACE_DELAY_SEC = 0.05


def _write_json(path: Path, data: str) -> None:
    """先寫暫存檔再換名。Windows 上目標檔可能被防毒或索引程式短暫鎖住（WinError 5），稍等重試。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f"{path.stem}.{uuid.uuid4().hex[:8]}.tmp")
    temp.write_text(data, encoding="utf-8")
    for attempt in range(1, REPLACE_ATTEMPTS + 1):
        try:
            temp.replace(path)
            return
        except PermissionError:
            if attempt == REPLACE_ATTEMPTS:
                temp.unlink(missing_ok=True)
                raise
            time.sleep(REPLACE_DELAY_SEC * attempt)


class SkillStore:
    def __init__(self, root: Path):
        self._root = root.resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    def _dir(self, skill_id: str) -> Path:
        if not _SAFE_ID.match(skill_id):
            raise SkillNotFound(skill_id)
        matches = list(self._root.glob(f"*/skills/{skill_id}"))
        if not matches:
            raise SkillNotFound(skill_id)
        return matches[0]

    def _save(self, meta: SkillMeta, version: SkillVersion | None = None) -> None:
        folder = self._root / meta.target_id / "skills" / meta.skill_id
        if version:
            _write_json(folder / f"v{version.version}.json", version.model_dump_json(indent=2))
        _write_json(folder / "meta.json", meta.model_dump_json(indent=2))

    def create(self, name: str, instruction: str, start_url: str, steps: Sequence[SkillStep],
               source_task_id: str | None, now: datetime) -> SkillMeta:
        skill_id = uuid.uuid4().hex[:12]
        meta = SkillMeta(skill_id=skill_id, name=name.strip(), target_id=target_id_for(start_url),
                         start_url=start_url, instruction=instruction, created_at=now, updated_at=now)
        version = SkillVersion(skill_id=skill_id, version=1, start_url=start_url, steps=tuple(steps),
                               source_task_id=source_task_id, created_at=now)
        self._save(meta, version)
        return meta

    def meta(self, skill_id: str) -> SkillMeta:
        try:
            return SkillMeta.model_validate_json((self._dir(skill_id) / "meta.json").read_text(encoding="utf-8"))
        except (ValidationError, OSError) as error:
            raise SkillFormatError(f"技能 {skill_id} 的資料損壞") from error

    def version(self, skill_id: str, number: int | None = None) -> SkillVersion:
        meta = self.meta(skill_id)
        path = self._dir(skill_id) / f"v{number or meta.current_version}.json"
        try:
            return SkillVersion.model_validate_json(path.read_text(encoding="utf-8"))
        except (ValidationError, OSError, json.JSONDecodeError) as error:
            raise SkillFormatError("技能檔格式不相容或已損壞，請重新錄製") from error

    def list(self) -> list[SkillMeta]:
        metas = []
        for path in self._root.glob("*/skills/*/meta.json"):
            try:
                metas.append(SkillMeta.model_validate_json(path.read_text(encoding="utf-8")))
            except (ValidationError, OSError):
                continue  # 損壞的技能不影響清單
        return sorted(metas, key=lambda m: m.updated_at, reverse=True)

    def add_version(self, skill_id: str, steps: Sequence[SkillStep], source_task_id: str | None,
                    now: datetime) -> SkillMeta:
        """自我修復成功後產生新版本（FR-404、FR-306）；舊版本保留。"""
        meta = self.meta(skill_id)
        number = meta.current_version + 1
        version = SkillVersion(skill_id=skill_id, version=number, start_url=meta.start_url, steps=tuple(steps),
                               source_task_id=source_task_id, created_at=now)
        updated = meta.model_copy(update={"current_version": number, "consecutive_failures": 0, "updated_at": now})
        self._save(updated, version)
        return updated

    def record_result(self, skill_id: str, ok: bool, now: datetime) -> SkillMeta:
        meta = self.meta(skill_id)
        update = ({"success_count": meta.success_count + 1, "consecutive_failures": 0} if ok else
                  {"fail_count": meta.fail_count + 1, "consecutive_failures": meta.consecutive_failures + 1})
        updated = meta.model_copy(update={**update, "updated_at": now})
        self._save(updated)
        return updated

    def delete(self, skill_id: str) -> None:
        shutil.rmtree(self._dir(skill_id))
