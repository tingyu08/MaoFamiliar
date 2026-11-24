from types import SimpleNamespace

from agent_fakes import FakeExecutor, Harness, ScriptedClient, ScriptedDecisions, call, later, turn

from familiar.core.models import ActionKind
from familiar.core.tasks import TaskStatus
from familiar.executors.elements import Element, ElementTable
from familiar.gemini.decisions import result_from_response
from familiar.knowledge.decision_actor import candidates_of, next_question

SUBMIT = Element("e1", "button", "送出", 10, 10, 60, 40, css="#go")
HELP = Element("e2", "link", "說明", 70, 10, 120, 40)
NAME = Element("e3", "textbox", "你的名字", 10, 60, 120, 80)
PW = Element("e4", "textbox", "", 10, 90, 120, 110, is_password=True)


def executor(**kwargs):
    return FakeExecutor(elements=(NAME, SUBMIT, HELP, PW), **kwargs)


def test_candidates_exclude_inputs_and_passwords():
    assert candidates_of(ElementTable((NAME, SUBMIT, HELP, PW)), 60) == (SUBMIT, HELP)
    assert candidates_of(ElementTable((SUBMIT, HELP)), 1) == (SUBMIT,)
    assert candidates_of(None, 10) == ()


def test_question_lists_candidates_and_none():
    question = next_question("送出表單", ["填名字 → 成功"], (SUBMIT, HELP))
    assert question["type"] == "choice"
    assert question["criteria"]["m1"] == "黃色標記 1：button「送出」"
    assert "none" in question["criteria"] and "填名字" in question["instructions"]


def test_result_from_sdk_response():
    response = SimpleNamespace(
        answers={"next": SimpleNamespace(type="choice", choice="m2", noul=None, confidence=0.9,
                                         probabilities={"m2": 0.9})},
        usage=SimpleNamespace(total_input_tokens=1300))
    result = result_from_response(response)
    assert result.answers["next"].choice == "m2" and result.tokens_in == 1300


async def test_actor_clicks_then_agent_finishes():
    decisions = ScriptedDecisions(("m1", 0.95), 0.1, ("none", 0.97))
    h = Harness(ScriptedClient(turn(call("task_done", summary="已送出"))), executor=executor(), decisions=decisions)
    result = await h.run()
    assert result.status is TaskStatus.SUCCEEDED
    assert [s.layer for s in h.steps] == ["actor", "agent"]
    actor_step = h.steps[0]
    assert actor_step.action["kind"] == "click_element" and actor_step.action["args"] == {"ref": "e1"}
    assert actor_step.model == "decision-1" and actor_step.decision_calls == 2
    assert h.steps[1].decision_calls == 1  # 回答 none 的那次記在代理人步驟上
    assert [a.kind for a in h.executor.executed] == [ActionKind.CLICK_ELEMENT]
    agent_request = h.client.requests[0]
    assert agent_request["previous"] is None and "第 3 層" in agent_request["input"][0]["text"]
    assert decisions.requests[0]["png"] != decisions.requests[1]["png"]  # 風險檢查只標出選中的元素


async def test_low_confidence_goes_to_agent():
    decisions = ScriptedDecisions(("m1", 0.6))
    h = Harness(ScriptedClient(turn(text="完成")), executor=executor(), decisions=decisions)
    await h.run()
    assert h.executor.executed == [] and len(h.client.requests) == 1


async def test_risky_choice_needs_confirmation():
    def approve(event):
        if event["type"] == "confirm_request":
            later(lambda: h.control.resolve_confirmation(event["request_id"], True))

    decisions = ScriptedDecisions(("m1", 0.95), 0.9)
    h = Harness(ScriptedClient(turn(text="完成")), executor=executor(), decisions=decisions, on_event=approve)
    await h.run()
    assert "confirm_request" in h.sink.types()
    assert h.steps[0].action["risk"] == "high" and h.steps[0].ok


async def test_unchanged_screen_skips_actor():
    decisions = ScriptedDecisions(("m1", 0.95), 0.1)
    h = Harness(ScriptedClient(turn(text="完成")), executor=executor(static_screen=True), decisions=decisions)
    await h.run()
    assert len(decisions.requests) == 2  # 第二步畫面沒變 → 不再問 Decisions
    assert [s.layer for s in h.steps] == ["actor"]


async def test_unchanged_screen_after_agent_step_still_uses_actor():
    """代理人輸入文字後畫面幾乎沒變（雜湊偵測不到），第 3 層仍然要試。"""
    decisions = ScriptedDecisions(("none", 0.95), ("m1", 0.95), 0.1)
    h = Harness(ScriptedClient(turn(call("fill_element", ref="e3", text="小明")), turn(text="完成")),
                executor=executor(static_screen=True), decisions=decisions)
    await h.run()
    assert [s.layer for s in h.steps] == ["agent", "actor"]


async def test_decision_errors_fall_back_to_agent():
    h = Harness(ScriptedClient(turn(text="完成")), executor=executor(),
                decisions=ScriptedDecisions(RuntimeError("decisions down")))
    result = await h.run()
    assert result.status is TaskStatus.SUCCEEDED and len(h.client.requests) == 1


async def test_no_candidates_means_no_decision_call():
    decisions = ScriptedDecisions()
    h = Harness(ScriptedClient(turn(text="完成")), executor=FakeExecutor(elements=(NAME,)), decisions=decisions)
    await h.run()
    assert decisions.requests == []
