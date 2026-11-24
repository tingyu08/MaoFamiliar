"""冒煙測試：用真的 Gemini（免費額度）＋真的 Decisions API 操作本機測試網頁。預設不執行。

執行：uv run pytest -m live -s tests/live
流程：AI 完成表單任務（可能由第 3 層代勞點擊）→ 存成技能 → 重播（不應呼叫代理人模型）。
大約消耗代理人模型 2–6 次、Decisions 2–8 次請求。透過 WebSocket 扮演使用者：收到確認請求就核准。
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Callable

import pytest
from dotenv import load_dotenv
from fastapi.testclient import TestClient

from familiar.api.app import create_app
from familiar.api.context import ServerSettings
from familiar.config import load_config
from familiar.gemini.decisions import GeminiDecisionClient
from familiar.gemini.interactions import GeminiInteractionsClient
from familiar.main import build_services

pytestmark = pytest.mark.live

BACKEND = Path(__file__).parents[2]
TOKEN = "live-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
INSTRUCTION = "在「你的名字」欄位輸入「小明」，然後按「送出表單」按鈕，最後告訴我頁面右邊顯示的結果文字。"
MAX_EVENTS = 300
LAYER_LABEL = {"agent": "代理人", "actor": "第3層", "skill": "重播"}


@pytest.fixture
def gemini_clients():
    load_dotenv(BACKEND / ".env")
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        pytest.skip("backend/.env 沒有 GEMINI_API_KEY")
    from google import genai

    options = {"timeout": 90_000, "retry_options": {"attempts": 1}}
    agent = GeminiInteractionsClient(genai.Client(api_key=key, http_options=options))
    decisions = GeminiDecisionClient(genai.Client(api_key=key, http_options={**options, "api_version": "v1beta"}))
    return agent, decisions


def drive(ws, start: Callable[[], str]) -> tuple[str, list[str]]:
    """開始任務並扮演使用者，直到任務結束。回傳 (task_id, 事件紀錄)。"""
    log: list[str] = []
    task_id = start()
    for _ in range(MAX_EVENTS):
        event = ws.receive_json()
        kind = event["type"]
        if kind == "confirm_request":
            log.append(f"確認請求：{event['action']['intent']}（{event['reason']}）→ 核准")
            ws.send_json({"type": "confirm_response", "request_id": event["request_id"], "approved": True})
        elif kind == "user_help_request":
            log.append(f"模型求助：{event['reason']} → 停止任務")
            ws.send_json({"type": "stop", "task_id": task_id})
        elif kind in ("waiting_quota", "skill_heal"):
            log.append(f"{kind}：{event.get('ready_at') or event.get('reason')}")
        elif kind == "quota_update":
            log.append(f"呼叫代理人模型：{event['model']}")
        elif kind == "task_finished":
            log.append(f"任務結束：{event['status']}")
            break
    return task_id, log


def report(title: str, detail: dict, log: list[str], seconds: float) -> None:
    task, steps = detail["task"], detail["steps"]
    print(f"\n=== {title} ===")
    print(f"狀態：{task['status']}　耗時：{seconds:.1f} 秒　步數：{task['steps_count']}　"
          f"代理人呼叫：{task['agent_calls']}　Decisions 呼叫：{task.get('decision_calls')}")
    print(f"結果：{task['result_text']}\n錯誤：{task['error_message']}")
    print("事件：\n  " + "\n  ".join(log))
    for s in steps:
        verdict = "成功" if s["result"]["ok"] else "失敗"
        print(f"  {s['idx']} [{LAYER_LABEL.get(s['layer'], s['layer'])}|{s['model']}] {s['action']['kind']} "
              f"{s['action']['args']} risk={s['action'].get('risk')} → {verdict} {s['result']['message']}"
              f"（{s['duration_ms']} ms）")


def test_real_models_record_and_replay(tmp_path, page_server, gemini_clients):
    agent, decisions = gemini_clients
    services = build_services(load_config(BACKEND / "config.yaml"), tmp_path, agent, headless=True,
                              decisions=decisions)
    settings = ServerSettings(token=TOKEN, allowed_hosts=("127.0.0.1", "localhost", "testserver"))
    url = f"{page_server}/form.html"
    with TestClient(create_app(services, settings, on_shutdown=services.close)) as client:
        with client.websocket_connect("/ws") as ws:
            ws.send_json({"type": "auth", "token": TOKEN})
            ws.receive_json()  # hello

            started = time.time()
            task_id, log = drive(ws, lambda: client.post(
                "/api/tasks", json={"url": url, "instruction": INSTRUCTION}, headers=AUTH).json()["data"]["id"])
            first = client.get(f"/api/tasks/{task_id}", headers=AUTH).json()["data"]
            report("第一次：AI 操作", first, log, time.time() - started)
            assert first["task"]["status"] == "succeeded", first["task"]["error_message"]
            assert "小明" in (first["task"]["result_text"] or "")

            saved = client.post("/api/skills", json={"task_id": task_id, "name": "填名字並送出"}, headers=AUTH)
            assert saved.status_code == 201, saved.json()
            skill_id = saved.json()["data"]["skill_id"]
            print(f"\n存成技能：{saved.json()['data']['step_count']} 步")

            started = time.time()
            replay_id, replay_log = drive(ws, lambda: client.post(
                f"/api/skills/{skill_id}/run", headers=AUTH).json()["data"]["id"])
            second = client.get(f"/api/tasks/{replay_id}", headers=AUTH).json()["data"]
            report("第二次：重播技能", second, replay_log, time.time() - started)

    usage = {m: q["day_requests"] for m, q in services.router.snapshot().items() if q["day_requests"]}
    print(f"\n本次配額用量：{usage}")
    assert second["task"]["status"] == "succeeded", second["task"]["error_message"]
    assert second["task"]["agent_calls"] == 0
