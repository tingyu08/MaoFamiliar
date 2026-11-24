"""第 2 週驗收：真的 Chromium ＋ 照劇本回應的假 Gemini，經由 API 跑完整個網頁任務。

模型劇本會讀取它收到的元素清單來決定 ref（不寫死編號），模擬真實模型的行為。
"""

from __future__ import annotations

import re
import time
from pathlib import Path

from fastapi.testclient import TestClient

from familiar.api.app import create_app
from familiar.api.context import ServerSettings
from familiar.config import load_config
from familiar.gemini.interactions import AgentTurn
from familiar.main import build_services

TOKEN = "e2e-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
CONFIG = Path(__file__).parents[2] / "config.yaml"


def find_ref(input_blocks, name: str) -> str:
    texts = []
    for block in input_blocks:
        if block.get("type") == "text":
            texts.append(block["text"])
        for part in block.get("result", []) if isinstance(block.get("result"), list) else []:
            if part.get("type") == "text":
                texts.append(part["text"])
    match = re.search(rf"(e\d+) \w+「{re.escape(name)}」", "\n".join(texts).replace("\\n", "\n"))
    assert match, f"模型收到的內容裡找不到元素「{name}」"
    return match.group(1)


class FormFillingModel:
    """第 1 輪填名字、第 2 輪按送出、第 3 輪讀結果並結束。"""

    def __init__(self):
        self.turns = 0
        self.models: list[str] = []

    async def create_turn(self, *, model, system_instruction, input, tools, previous_interaction_id):
        self.turns += 1
        self.models.append(model)
        assert "密碼" in system_instruction
        if self.turns == 1:
            ref = find_ref(input, "你的名字")
            call = {"id": "a", "name": "fill_element", "arguments": {"ref": ref, "text": "小明", "intent": "填名字"}}
        elif self.turns == 2:
            ref = find_ref(input, "送出表單")
            call = {"id": "b", "name": "click_element", "arguments": {"ref": ref, "intent": "送出"}}
        else:
            call = {"id": "c", "name": "task_done", "arguments": {"summary": "表單已送出", "intent": "完成"}}
        return AgentTurn(interaction_id=f"i{self.turns}", function_calls=(call,), text="", tokens_in=1200)


def test_web_task_end_to_end(tmp_path, page_server):
    model = FormFillingModel()
    services = build_services(load_config(CONFIG), tmp_path, model, headless=True)
    settings = ServerSettings(token=TOKEN, allowed_hosts=("127.0.0.1", "localhost", "testserver"))
    with TestClient(create_app(services, settings, on_shutdown=services.close)) as client:
        created = client.post("/api/tasks", json={"url": f"{page_server}/form.html", "instruction": "填名字並送出"},
                              headers=AUTH)
        assert created.status_code == 201
        task_id = created.json()["data"]["id"]
        deadline = time.time() + 60
        detail = {}
        while time.time() < deadline:
            detail = client.get(f"/api/tasks/{task_id}", headers=AUTH).json()["data"]
            if detail["task"]["status"] not in ("running", "pending"):
                break
            time.sleep(0.2)

    assert detail["task"]["status"] == "succeeded", detail["task"]
    assert detail["task"]["result_text"] == "表單已送出"
    assert [s["action"]["kind"] for s in detail["steps"]] == ["fill_element", "click_element", "task_done"]
    assert all(s["result"]["ok"] for s in detail["steps"])
    assert model.models[0] == "gemini-3.5-flash-lite"  # 依工作分工：操作鏈第一個
    quota = services.router.snapshot()
    assert quota["gemini-3.5-flash-lite"]["day_requests"] == 3
