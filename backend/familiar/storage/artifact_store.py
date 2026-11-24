"""截圖檔案存取（只存在本機 data/ 底下）。"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class ArtifactStore:
    def __init__(self, root: Path):
        self._root = root.resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    def _inside(self, relative: str) -> Path:
        path = (self._root / relative).resolve()
        if self._root not in path.parents:
            raise ValueError(f"路徑不在截圖資料夾內：{relative}")
        return path

    def save_screenshot(self, task_id: str, idx: int, png: bytes) -> str:
        """回傳相對路徑（存進資料庫用）。"""
        if not _SAFE_ID.match(task_id):
            raise ValueError(f"不合法的任務 ID：{task_id}")
        relative = f"{task_id}/{idx:04d}.png"
        path = self._inside(relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(png)
        return relative

    def read(self, relative: str) -> bytes:
        return self._inside(relative).read_bytes()

    def delete_task(self, task_id: str) -> None:
        if not _SAFE_ID.match(task_id):
            raise ValueError(f"不合法的任務 ID：{task_id}")
        shutil.rmtree(self._inside(task_id), ignore_errors=True)
