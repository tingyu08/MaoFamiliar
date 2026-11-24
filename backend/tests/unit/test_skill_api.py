import time

from agent_fakes import FakeExecutor, ScriptedClient, call, turn
from fastapi.testclient import TestClient
from test_replay import NAME, SUBMIT, URL, Harness

from familiar.api.app import create_app
from familiar.api.context import ServerSettings, Services
from familiar.core.events import EventBus

TOKEN = "t"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def build(tmp_path, client):
    h = Harness(tmp_path, client=client, executor=FakeExecutor(elements=(NAME, SUBMIT)))
    h.runner._events = EventBus()
    services = Services(runner=h.runner, repo=h.repo, artifacts=None, router=None, events=h.runner._events,
                        skills=h.skills)
    settings = ServerSettings(token=TOKEN, allowed_hosts=("testserver",))
    return TestClient(create_app(services, settings)), h


def wait(client, task_id):
    for _ in range(200):
        task = client.get(f"/api/tasks/{task_id}", headers=AUTH).json()["data"]["task"]
        if task["status"] in ("succeeded", "failed", "stopped"):
            return task
        time.sleep(0.05)
    raise AssertionError("任務沒有結束")


def test_record_list_run_and_delete(tmp_path):
    client_model = ScriptedClient(turn(call("fill_element", ref="e1", text="小明")),
                                  turn(call("click_element", "c2", ref="e2")),
                                  turn(call("task_done", "c3", summary="已送出")))
    client, h = build(tmp_path, client_model)
    with client:
        task_id = client.post("/api/tasks", json={"url": URL, "instruction": "填名字並送出"}, headers=AUTH).json()["data"]["id"]
        assert wait(client, task_id)["status"] == "succeeded"

        created = client.post("/api/skills", json={"task_id": task_id, "name": "送出表單"}, headers=AUTH)
        assert created.status_code == 201
        skill = created.json()["data"]
        assert skill["step_count"] == 2 and skill["needs_relearn"] is False

        assert [s["skill_id"] for s in client.get("/api/skills", headers=AUTH).json()["data"]] == [skill["skill_id"]]
        detail = client.get(f"/api/skills/{skill['skill_id']}", headers=AUTH).json()["data"]
        assert [s["kind"] for s in detail["steps"]] == ["fill_element", "click_element"]

        run = client.post(f"/api/skills/{skill['skill_id']}/run", headers=AUTH)
        assert run.status_code == 201
        replayed = wait(client, run.json()["data"]["id"])
        assert replayed["status"] == "succeeded" and replayed["agent_calls"] == 0

        assert client.delete(f"/api/skills/{skill['skill_id']}", headers=AUTH).status_code == 200
        assert client.get(f"/api/skills/{skill['skill_id']}", headers=AUTH).status_code == 404


def test_errors(tmp_path):
    client, h = build(tmp_path, ScriptedClient(turn(call("ask_user", reason="登入")), turn(text="ok")))
    with client:
        assert client.post("/api/skills", json={"task_id": "nope", "name": "x"}, headers=AUTH).status_code == 404
        assert client.post("/api/skills", json={"task_id": "x", "name": ""}, headers=AUTH).status_code == 422
        assert client.post("/api/skills/0123456789ab/run", headers=AUTH).status_code == 404
        assert client.delete("/api/skills/0123456789ab", headers=AUTH).status_code == 404
        assert client.get("/api/skills", headers={}).status_code == 401
