"""示範／E2E 用伺服器：真的後端＋前端＋Chromium，但模型是照劇本回應的假模型（不花額度）。

單獨執行：uv run python tests/e2e/demo_server.py   → 印出可開啟的網址
劇本：填「你的名字」→ 按「送出表單」（標成高風險，會跳確認窗）→ 回報結果。
"""

from __future__ import annotations

import functools
import re
import sys
import threading
import time
from dataclasses import dataclass
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import uvicorn

BACKEND = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND))

from familiar.api.app import create_app  # noqa: E402
from familiar.api.context import ServerSettings  # noqa: E402
from familiar.config import load_config  # noqa: E402
from familiar.gemini.interactions import AgentTurn  # noqa: E402
from familiar.main import build_services  # noqa: E402

PAGES = BACKEND / "tests" / "fixtures" / "pages"
FRONTEND_DIST = BACKEND.parent / "frontend" / "dist"
DEMO_TOKEN = "demo-token"


def _texts(blocks: Any) -> str:
    texts = []
    for block in blocks:
        if block.get("type") == "text":
            texts.append(block["text"])
        for part in block.get("result", []) if isinstance(block.get("result"), list) else []:
            if part.get("type") == "text":
                texts.append(part["text"])
    return "\n".join(texts).replace("\\n", "\n")


def find_ref(blocks: Any, name: str) -> str:
    match = re.search(rf"(e\d+) \w+「{re.escape(name)}」", _texts(blocks))
    if not match:
        raise AssertionError(f"模型收到的內容裡找不到元素「{name}」")
    return match.group(1)


class ScriptedFormModel:
    """照劇本操作 form.html；每輪之間可以加延遲，讓畫面看得到變化。"""

    def __init__(self, delay: float = 0.0, risky_submit: bool = True):
        self.delay = delay
        self.risky_submit = risky_submit
        self.turns = 0

    async def create_turn(self, *, model, system_instruction, input, tools, previous_interaction_id):
        import asyncio

        await asyncio.sleep(self.delay)
        self.turns += 1
        if self.turns == 1:
            call = {"name": "fill_element", "arguments": {"ref": find_ref(input, "你的名字"), "text": "小明",
                                                          "intent": "在名字欄位輸入小明"}}
        elif self.turns == 2:
            call = {"name": "click_element", "arguments": {"ref": find_ref(input, "送出表單"), "intent": "按下送出表單",
                                                           "risk": "high" if self.risky_submit else "low"}}
        else:
            call = {"name": "task_done", "arguments": {"summary": "頁面顯示：已點擊:小明", "intent": "回報結果"}}
        return AgentTurn(interaction_id=f"demo-{self.turns}", function_calls=({"id": f"c{self.turns}", **call},),
                         text="", tokens_in=1500)


@dataclass
class DemoServer:
    base_url: str
    page_url: str
    token: str
    stop: Any


def _start_pages() -> ThreadingHTTPServer:
    handler = functools.partial(SimpleHTTPRequestHandler, directory=str(PAGES))
    handler.log_message = lambda *a, **k: None  # type: ignore[method-assign]
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def start_demo(data_dir: Path, port: int, model: Any | None = None, headless: bool = True) -> DemoServer:
    pages = _start_pages()
    services = build_services(load_config(BACKEND / "config.yaml"), data_dir, model or ScriptedFormModel(),
                              headless=headless)
    settings = ServerSettings(port=port, token=DEMO_TOKEN)
    app = create_app(services, settings, frontend_dir=FRONTEND_DIST, on_shutdown=services.close)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 15
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    if not server.started:
        raise RuntimeError("示範伺服器沒有在 15 秒內啟動")

    def stop() -> None:
        server.should_exit = True
        thread.join(timeout=10)
        pages.shutdown()
        pages.server_close()

    return DemoServer(base_url=f"http://127.0.0.1:{port}", page_url=f"http://127.0.0.1:{pages.server_address[1]}",
                      token=DEMO_TOKEN, stop=stop)


if __name__ == "__main__":
    import tempfile

    demo = start_demo(Path(tempfile.mkdtemp(prefix="familiar-demo-")), port=8766,
                      model=ScriptedFormModel(delay=1.5), headless=True)
    print(f"示範網址：{demo.base_url}/?token={demo.token}", flush=True)
    print(f"測試網頁：{demo.page_url}/form.html", flush=True)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        demo.stop()
