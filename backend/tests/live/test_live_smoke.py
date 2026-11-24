"""冒煙測試：用真的 Gemini（免費額度）操作本機測試網頁。預設不執行。

執行：uv run pytest -m live -s tests/live
大約消耗代理人模型 3–6 次請求。透過 WebSocket 扮演使用者：收到確認請求就核准（並記錄）。
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest
from dotenv import load_dotenv
from fastapi.testclient import TestClient

from familiar.api.app import create_app
from familiar.api.context import ServerSettings
from familiar.config import load_config
from familiar.gemini.interactions import GeminiInteractionsClient
from familiar.main import build_services

pytestmark = pytest.mark.live

BACKEND = Path(__file__).parents[2]
TOKEN = "live-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
INSTRUCTION = "在「你的名字」欄位輸入「小明」，然後按「送出表單」按鈕，最後告訴我頁面右邊顯示的結果文字。"
MAX_EVENTS = 300


@pytest.fixture
def gemini_client():
    load_dotenv(BACKEND / ".env")
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        pytest.skip("backend/.env 沒有 GEMINI_API_KEY")
    from google import genai

    return GeminiInteractionsClient(
        genai.Client(api_key=key, http_options={"timeout": 90_000, "retry_options": {"attempts": 1}}))


def drive_task(client, ws, url) -> tuple[str, list[str]]:
    """建立任務並扮演使用者，直到任務結束。回傳 (task_id, 事件紀錄)。"""
    log: list[str] = []
    task_id = client.post("/api/tasks", json={"url": url, "instruction": INSTRUCTION}, headers=AUTH).json()["data"]["id"]
    for _ in range(MAX_EVENTS):
        event = ws.receive_json()
        kind = event["type"]
        if kind == "confirm_request":
            log.append(f"確認請求：{event['action']['kind']}（{event['action']['intent']}）原因={event['reason']} → 核准")
            ws.send_json({"type": "confirm_response", "request_id": event["request_id"], "approved": True})
        elif kind == "user_help_request":
            log.append(f"模型求助：{event['reason']} → 停止任務")
            ws.send_json({"type": "stop", "task_id": task_id})
        elif kind == "waiting_quota":
            log.append(f"等待配額：{event['ready_at']}")
        elif kind == "quota_update":
            log.append(f"呼叫模型：{event['model']}")
        elif kind == "task_finished":
            log.append(f"任務結束：{event['status']}")
            break
    return task_id, log


def test_real_model_fills_form(tmp_path, page_server, gemini_client):
    services = build_services(load_config(BACKEND / "config.yaml"), tmp_path, gemini_client, headless=True)
    settings = ServerSettings(token=TOKEN, allowed_hosts=("127.0.0.1", "localhost", "testserver"))
    started = time.time()
    with TestClient(create_app(services, settings, on_shutdown=services.close)) as client:
        with client.websocket_connect("/ws") as ws:
            ws.send_json({"type": "auth", "token": TOKEN})
            ws.receive_json()  # hello
            task_id, log = drive_task(client, ws, f"{page_server}/form.html")
        detail = client.get(f"/api/tasks/{task_id}", headers=AUTH).json()["data"]

    task, steps = detail["task"], detail["steps"]
    print(f"\n狀態：{task['status']}　耗時：{time.time() - started:.1f} 秒　步數：{task['steps_count']}")
    print(f"結果：{task['result_text']}\n錯誤：{task['error_message']}")
    print("事件：\n  " + "\n  ".join(log))
    print("步驟：")
    for s in steps:
        verdict = "成功" if s["result"]["ok"] else "失敗：" + s["result"]["message"]
        print(f"  {s['idx']} [{s['model']}] {s['action']['kind']} {s['action']['args']} risk={s['action'].get('risk')}"
              f" → {verdict}（{s['duration_ms']} ms，輸入 {s['tokens_in']} token）")
    usage = {m: q["day_requests"] for m, q in services.router.snapshot().items() if q["day_requests"]}
    print(f"本次配額用量：{usage}")

    assert task["status"] == "succeeded", task["error_message"]
    kinds = [s["action"]["kind"] for s in steps]
    assert any(k in ("fill_element", "type_text") for k in kinds)
    assert any(k in ("click_element", "click") for k in kinds)
    assert "小明" in (task["result_text"] or "")
