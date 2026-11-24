import json

from agent_fakes import ApiError, FakeExecutor, Harness, ScriptedClient, call, later, turn

from familiar.core.action_handler import PASSWORD_MESSAGE
from familiar.core.agent_types import LoopConfig
from familiar.core.models import ActionKind
from familiar.core.tasks import TaskStatus


def result_payload(request, index=-1):
    """取出送給模型的 function_result 文字內容。"""
    step = request["input"][index]
    assert step["type"] == "function_result"
    return step, json.loads(step["result"][0]["text"])


async def test_no_function_calls_means_done():
    h = Harness(ScriptedClient(turn(text="公告標題是 X")))
    result = await h.run()
    assert result.status is TaskStatus.SUCCEEDED and result.text == "公告標題是 X"
    first = h.client.requests[0]
    assert first["previous"] is None
    assert first["input"][0]["type"] == "text" and "送出表單" in first["input"][0]["text"]
    assert "e1 button「送出」" in first["input"][0]["text"]
    assert first["input"][1]["type"] == "image"


async def test_click_then_done_continues_conversation():
    h = Harness(ScriptedClient(turn(call("click_element", ref="e1"), interaction_id="i1"),
                               turn(call("task_done", "c2", summary="已送出"), interaction_id="i2")))
    result = await h.run()
    assert result.status is TaskStatus.SUCCEEDED and result.text == "已送出" and result.steps == 2
    assert [a.kind for a in h.executor.executed] == [ActionKind.CLICK_ELEMENT]
    second = h.client.requests[1]
    assert second["previous"] == "i1"
    step, payload = result_payload(second)
    assert payload["ok"] is True and payload["url"] == "https://example.test/form"
    assert "e1 button" in payload["elements"]
    assert step["result"][1]["type"] == "image"
    assert [s.index for s in h.steps] == [0, 1]
    assert h.steps[0].tokens_in == 100
    assert h.sink.types().count("step") == 2 and "quota_update" in h.sink.types()


async def test_invalid_call_is_reported_back():
    h = Harness(ScriptedClient(turn(call("click", x=5000, y=1)), turn(text="放棄")))
    await h.run()
    step, payload = result_payload(h.client.requests[1])
    assert step["is_error"] is True and "x" in payload["error"]
    assert h.executor.executed == []


async def test_high_risk_confirmed():
    def approve(event):
        if event["type"] == "confirm_request":
            later(lambda: h.control.resolve_confirmation(event["request_id"], True))

    h = Harness(ScriptedClient(turn(call("click", x=10, y=10, risk="high")), turn(text="ok")), on_event=approve)
    await h.run()
    assert len(h.executor.executed) == 1
    assert TaskStatus.WAITING_USER in h.statuses and h.statuses[-1] is TaskStatus.RUNNING


async def test_high_risk_denied():
    def deny(event):
        if event["type"] == "confirm_request":
            later(lambda: h.control.resolve_confirmation(event["request_id"], False))

    h = Harness(ScriptedClient(turn(call("click", x=10, y=10, risk="high")), turn(text="ok")), on_event=deny)
    await h.run()
    assert h.executor.executed == []
    _, payload = result_payload(h.client.requests[1])
    assert "拒絕" in payload["error"]


async def test_password_typing_is_blocked():
    h = Harness(ScriptedClient(turn(call("type_text", text="secret")), turn(text="ok")),
                executor=FakeExecutor(password=True))
    await h.run()
    assert h.executor.executed == []
    _, payload = result_payload(h.client.requests[1])
    assert payload["error"] == PASSWORD_MESSAGE


async def test_ask_user_waits_for_help():
    def help_done(event):
        if event["type"] == "user_help_request":
            later(lambda: h.control.resolve_user_help(event["request_id"]))

    h = Harness(ScriptedClient(turn(call("ask_user", reason="請登入")), turn(text="ok")), on_event=help_done)
    result = await h.run()
    assert result.status is TaskStatus.SUCCEEDED
    _, payload = result_payload(h.client.requests[1])
    assert payload["user_done"] is True
    assert h.statuses == [TaskStatus.WAITING_USER, TaskStatus.RUNNING]


async def test_stop_while_waiting_for_help():
    def stop(event):
        if event["type"] == "user_help_request":
            later(h.control.request_stop)

    h = Harness(ScriptedClient(turn(call("ask_user", reason="?"))), on_event=stop)
    assert (await h.run()).status is TaskStatus.STOPPED


async def test_stopped_before_start():
    h = Harness(ScriptedClient())
    h.control.request_stop()
    assert (await h.run()).status is TaskStatus.STOPPED


async def test_step_limit():
    clicks = [turn(call("click", f"c{i}", x=1, y=1)) for i in range(5)]
    h = Harness(ScriptedClient(*clicks), config=LoopConfig(max_steps=2))
    result = await h.run()
    assert result.status is TaskStatus.FAILED and "步數上限" in result.error


async def test_stuck_screen_stops_task():
    clicks = [turn(call("click", f"c{i}", x=1, y=1)) for i in range(10)]
    h = Harness(ScriptedClient(*clicks), executor=FakeExecutor(static_screen=True),
                config=LoopConfig(stuck_warn_steps=2, stuck_stop_steps=3))
    result = await h.run()
    assert result.status is TaskStatus.FAILED and "沒有變化" in result.error
    _, payload = result_payload(h.client.requests[1])
    assert "note" in payload


async def test_model_switch_starts_fresh_conversation():
    daily = ApiError(429, "20 requests per day. Please retry in 30m0s")
    h = Harness(ScriptedClient(turn(call("click", x=1, y=1), interaction_id="i1"),
                               {"lite": daily},
                               turn(text="完成")))
    result = await h.run()
    assert result.status is TaskStatus.SUCCEEDED
    last = h.client.requests[-1]
    assert last["model"] == "flash" and last["previous"] is None
    assert "已經做過的步驟" in last["input"][0]["text"]


async def test_no_model_available_waits_then_stop():
    def stop(event):
        if event["type"] == "waiting_quota":
            later(h.control.request_stop)

    h = Harness(ScriptedClient(ApiError(429, "per day")), chain=("lite",), on_event=stop)
    result = await h.run()
    assert result.status is TaskStatus.STOPPED
    assert TaskStatus.WAITING_QUOTA in h.statuses


async def test_safety_block_retries_once_then_fails():
    blocked = ApiError(400, "Request blocked due to safety violations")
    h = Harness(ScriptedClient(blocked, blocked))
    result = await h.run()
    assert result.status is TaskStatus.FAILED and "安全機制" in result.error
    assert len(h.client.requests) == 2


async def test_unexpected_error_fails_with_friendly_message():
    h = Harness(ScriptedClient(RuntimeError("boom")))
    result = await h.run()
    assert result.status is TaskStatus.FAILED and result.error == "呼叫 AI 模型失敗，請稍後再試"


async def test_long_conversation_is_compressed():
    h = Harness(ScriptedClient(turn(call("click", x=1, y=1), interaction_id="i1"), turn(text="ok")),
                config=LoopConfig(compress_after_steps=1))
    await h.run()
    assert h.client.requests[1]["previous"] is None


async def test_planning_and_knowledge_actions():
    h = Harness(ScriptedClient(turn(call("complete_subgoal", "a", id="1"), call("save_note", "b", text="Esc 關閉")),
                               turn(text="ok")))
    await h.run()
    assert "plan_updated" in h.sink.types()
    payloads = [json.loads(s["result"][0]["text"]) for s in h.client.requests[1]["input"]]
    assert payloads[0]["ok"] is True and payloads[1]["ok"] is False
