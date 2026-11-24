"""共用 fixture：本機測試網頁伺服器、真實 Playwright 瀏覽器（headless）。"""

from __future__ import annotations

import functools
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

PAGES = Path(__file__).parent / "fixtures" / "pages"


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):  # 測試時不印存取紀錄
        pass


@pytest.fixture(autouse=True)
def close_databases(monkeypatch):
    """測試裡建立的 Database 在測試結束後一律關閉，避免 ResourceWarning。"""
    from familiar.storage.db import Database

    created = []
    original_init = Database.__init__

    def tracking_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        created.append(self)

    monkeypatch.setattr(Database, "__init__", tracking_init)
    yield
    for database in created:
        try:
            database.close()
        except Exception:  # noqa: BLE001 — 已關閉的連線再關一次沒關係
            pass


@pytest.fixture(scope="session")
def page_server():
    handler = functools.partial(_QuietHandler, directory=str(PAGES))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


@pytest.fixture
async def browser(page_server):
    from familiar.executors.browser import BrowserExecutor

    executor = BrowserExecutor(profile_dir=None, headless=True)
    await executor.start(f"{page_server}/form.html")
    yield executor
    await executor.close()
