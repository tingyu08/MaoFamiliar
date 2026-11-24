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
    """照劇本操作 form.html；每輪之間可以加延遲，讓畫面看得到變化。

    - 第 3 層已經按過送出（歷史裡有「點擊「送出表單」」）就直接回報完成。
    - heal_button 有設定時，收到自我修復的指示（「原本的任務」）就改按這個按鈕。
    """

    def __init__(self, delay: float = 0.0, risky_submit: bool = True, heal_button: str | None = None):
        self.delay = delay
        self.risky_submit = risky_submit
        self.heal_button = heal_button
        self.turns = 0
        self._stage = 0

    def _next_call(self, text: str, blocks: Any) -> dict[str, Any]:
        done = {"name": "task_done", "arguments": {"summary": "頁面顯示：已點擊:小明", "intent": "回報結果"}}
        if "點擊「送出表單」" in text or "點擊「提交表單」" in text:
            return done
        if self.heal_button and "原本的任務" in text and self._stage < 10:
            self._stage = 10
            return {"name": "click_element", "arguments": {"ref": find_ref(blocks, self.heal_button),
                                                           "intent": f"按下{self.heal_button}"}}
        self._stage += 1
        if self._stage == 1:
            return {"name": "fill_element", "arguments": {"ref": find_ref(blocks, "你的名字"), "text": "小明",
                                                          "intent": "在名字欄位輸入小明"}}
        if self._stage == 2:
            return {"name": "click_element", "arguments": {"ref": find_ref(blocks, "送出表單"), "intent": "按下送出表單",
                                                           "risk": "high" if self.risky_submit else "low"}}
        self._stage = 0  # 下一個任務重新開始
        return done

    async def create_turn(self, *, model, system_instruction, input, tools, previous_interaction_id):
        import asyncio

        await asyncio.sleep(self.delay)
        self.turns += 1
        call = self._next_call(_texts(input), input)
        if call["name"] == "task_done":
            self._stage = 0
        return AgentTurn(interaction_id=f"demo-{self.turns}", function_calls=({"id": f"c{self.turns}", **call},),
                         text="", tokens_in=1500)


class FormDecisions:
    """假的 Decisions API：名字填好之後，選「送出表單」；其他時候回答 none。"""

    def __init__(self, risky: bool = False):
        self.risky = risky
        self.calls = 0

    async def decide(self, *, model, image_png, questions):
        from familiar.gemini.decisions import Answer, DecisionResult

        self.calls += 1
        key, question = next(iter(questions.items()))
        if question["type"] == "noul":
            probability = 0.9 if self.risky else 0.1
            return DecisionResult({key: Answer("noul", probability=probability, confidence=0.9)}, tokens_in=1000)
        choice = "none"
        history = question["instructions"].split("已經做過的步驟")[-1]
        if "輸入小明" in history:
            choice = next((k for k, v in question["criteria"].items() if "送出表單" in v), "none")
        return DecisionResult({key: Answer("choice", choice=choice, confidence=0.95)}, tokens_in=1200)


@dataclass
class DemoServer:
    base_url: str
    page_url: str
    token: str
    stop: Any


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args: Any) -> None:
        pass

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")  # 測試會在中途修改網頁，不能被快取
        super().end_headers()


def _start_pages(pages_dir: Path) -> ThreadingHTTPServer:
    handler = functools.partial(_QuietHandler, directory=str(pages_dir))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def start_demo(data_dir: Path, port: int, model: Any | None = None, headless: bool = True,
               decisions: Any | None = None, pages_dir: Path = PAGES) -> DemoServer:
    pages = _start_pages(pages_dir)
    services = build_services(load_config(BACKEND / "config.yaml"), data_dir, model or ScriptedFormModel(),
                              headless=headless, decisions=decisions)
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
                      model=ScriptedFormModel(delay=1.5), headless=True, decisions=FormDecisions(risky=True))
    print(f"示範網址：{demo.base_url}/?token={demo.token}", flush=True)
    print(f"測試網頁：{demo.page_url}/form.html", flush=True)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        demo.stop()
