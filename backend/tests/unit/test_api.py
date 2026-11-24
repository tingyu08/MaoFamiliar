import time
from datetime import UTC, datetime

import pytest
from agent_fakes import ScriptedClient, call, turn
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from test_task_runner import URL, Env

from familiar.api.app import create_app
from familiar.api.context import ServerSettings, Services
from familiar.core.events import EventBus
from familiar.gemini.model_router import ModelRouter, Role, RouterConfig

TOKEN = "test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def build(tmp_path, client, frontend_dir=None):
    bus = EventBus()  # runner 與代理人迴圈的事件都進真正的匯流排（WebSocket 會訂閱）
    env = Env(tmp_path, client, sink=bus)

    async def no_sleep(_seconds):
        return None

    router = ModelRouter(RouterConfig(chains={Role.ACTION: ("lite",)}), clock=lambda: datetime.now(UTC),
                         sleep=no_sleep)
    services = Services(runner=env.runner, repo=env.repo, artifacts=env.artifacts, router=router, events=bus)
    settings = ServerSettings(port=8765, token=TOKEN, allowed_hosts=("127.0.0.1", "localhost", "testserver"))
    return TestClient(create_app(services, settings, frontend_dir)), env


def wait_final(client, task_id, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        data = client.get(f"/api/tasks/{task_id}", headers=AUTH).json()["data"]
        if data["task"]["status"] in ("succeeded", "failed", "stopped"):
            return data
        time.sleep(0.05)
    raise AssertionError("任務沒有在時間內結束")


def test_requires_token(tmp_path):
    client, _ = build(tmp_path, ScriptedClient())
    with client:
        response = client.get("/api/tasks")
        assert response.status_code == 401
        assert response.json() == {"success": False, "data": None, "error": "未授權：缺少或錯誤的 token"}
        assert client.get("/api/tasks", headers={"Authorization": "Bearer wrong"}).status_code == 401


def test_rejects_foreign_host(tmp_path):
    client, _ = build(tmp_path, ScriptedClient())
    with client:
        assert client.get("/api/tasks", headers={**AUTH, "host": "evil.example"}).status_code == 400


def test_full_task_flow(tmp_path):
    client, env = build(tmp_path, ScriptedClient(turn(call("click_element", ref="e1")),
                                                 turn(call("task_done", "c2", summary="完成了"))))
    with client:
        created = client.post("/api/tasks", json={"url": URL, "instruction": "送出"}, headers=AUTH)
        assert created.status_code == 201 and created.json()["success"] is True
        task_id = created.json()["data"]["id"]
        detail = wait_final(client, task_id)
        assert detail["task"]["result_text"] == "完成了"
        assert [s["idx"] for s in detail["steps"]] == [0, 1]
        listing = client.get("/api/tasks?limit=5", headers=AUTH).json()
        assert listing["meta"] == {"total": 1, "limit": 5, "offset": 0}
        shot = client.get(f"/api/tasks/{task_id}/steps/0/screenshot?token={TOKEN}")
        assert shot.status_code == 200 and shot.headers["content-type"] == "image/png"
        assert client.get(f"/api/tasks/{task_id}/steps/9/screenshot", headers=AUTH).status_code == 404


def test_realtime_mode_flag(tmp_path):
    client, env = build(tmp_path, ScriptedClient(turn(text="ok")))
    with client:
        created = client.post("/api/tasks", json={"url": URL, "instruction": "玩遊戲", "mode": "realtime"},
                              headers=AUTH).json()["data"]
        assert created["mode"] == "realtime"
        wait_final(client, created["id"])


@pytest.mark.parametrize("body,status", [
    ({"url": URL, "instruction": "a", "mode": "turbo"}, 422),
    ({"url": "javascript:x", "instruction": "a"}, 400),
    ({"url": URL}, 422),
    ({"url": URL, "instruction": "a", "extra": 1}, 422),
])
def test_invalid_create(tmp_path, body, status):
    client, _ = build(tmp_path, ScriptedClient())
    with client:
        response = client.post("/api/tasks", json=body, headers=AUTH)
        assert response.status_code == status and response.json()["success"] is False


def test_busy_and_stop(tmp_path):
    client, _ = build(tmp_path, ScriptedClient(turn(call("ask_user", reason="等"))))
    with client:
        task_id = client.post("/api/tasks", json={"url": URL, "instruction": "a"}, headers=AUTH).json()["data"]["id"]
        time.sleep(0.1)
        assert client.post("/api/tasks", json={"url": URL, "instruction": "b"}, headers=AUTH).status_code == 409
        assert client.post(f"/api/tasks/{task_id}/stop", headers=AUTH).status_code == 200
        assert wait_final(client, task_id)["task"]["status"] == "stopped"
        assert client.post(f"/api/tasks/{task_id}/stop", headers=AUTH).status_code == 409


def test_missing_task_and_quota(tmp_path):
    client, _ = build(tmp_path, ScriptedClient())
    with client:
        assert client.get("/api/tasks/nope", headers=AUTH).status_code == 404
        quota = client.get("/api/quota", headers=AUTH).json()
        assert quota["data"]["lite"]["status"] == "ready"


def test_websocket_rejects_bad_origin(tmp_path):
    client, _ = build(tmp_path, ScriptedClient())
    with client, pytest.raises(WebSocketDisconnect) as info:
        with client.websocket_connect("/ws", headers={"origin": "https://evil.example"}) as ws:
            ws.receive_json()
    assert info.value.code == 4403


def test_websocket_requires_auth(tmp_path):
    client, _ = build(tmp_path, ScriptedClient())
    with client, client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "auth", "token": "wrong"})
        with pytest.raises(WebSocketDisconnect) as info:
            ws.receive_json()
        assert info.value.code == 4401


def test_websocket_events_and_confirmation(tmp_path):
    script = ScriptedClient(turn(call("click", x=1, y=1, risk="high")), turn(text="好了"))
    client, _ = build(tmp_path, script)
    with client, client.websocket_connect("/ws", headers={"origin": "http://127.0.0.1:8765"}) as ws:
        ws.send_json({"type": "auth", "token": TOKEN})
        assert ws.receive_json()["type"] == "hello"
        client.post("/api/tasks", json={"url": URL, "instruction": "a"}, headers=AUTH)
        seen = []
        for _ in range(50):
            event = ws.receive_json()
            seen.append(event["type"])
            if event["type"] == "confirm_request":
                ws.send_json({"type": "confirm_response", "request_id": event["request_id"], "approved": True})
            if event["type"] == "task_finished":
                assert event["status"] == "succeeded"
                break
        assert {"task_started", "confirm_request", "ack", "step"} <= set(seen)


def test_reconnect_receives_pending_confirmation(tmp_path):
    script = ScriptedClient(turn(call("click", x=1, y=1, risk="high")), turn(text="好了"))
    client, _ = build(tmp_path, script)
    with client:
        with client.websocket_connect("/ws") as first:
            first.send_json({"type": "auth", "token": TOKEN})
            first.receive_json()
            client.post("/api/tasks", json={"url": URL, "instruction": "a"}, headers=AUTH)
            for _ in range(50):
                if first.receive_json()["type"] == "confirm_request":
                    break
        # 模擬重新整理頁面：新連線的 hello 要帶著還沒處理的確認請求
        with client.websocket_connect("/ws") as second:
            second.send_json({"type": "auth", "token": TOKEN})
            hello = second.receive_json()
            assert hello["current_task_id"]
            [pending] = hello["pending"]
            assert pending["type"] == "confirm_request"
            second.send_json({"type": "confirm_response", "request_id": pending["request_id"], "approved": True})
            for _ in range(50):
                event = second.receive_json()
                if event["type"] == "task_finished":
                    assert event["status"] == "succeeded"
                    break


def test_websocket_bad_messages(tmp_path):
    client, _ = build(tmp_path, ScriptedClient())
    with client, client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "auth", "token": TOKEN})
        ws.receive_json()
        ws.send_json({"type": "confirm_response"})
        assert ws.receive_json()["type"] == "error"
        ws.send_json({"type": "dance"})
        assert ws.receive_json()["message"] == "不認得的訊息類型"
        ws.send_json({"type": "stop", "task_id": "nope"})
        assert ws.receive_json() == {"type": "ack", "for": "stop", "ok": False}


def test_serves_frontend(tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<h1>Familiar</h1>", encoding="utf-8")
    client, _ = build(tmp_path, ScriptedClient(), frontend_dir=dist)
    with client:
        assert "Familiar" in client.get("/").text
