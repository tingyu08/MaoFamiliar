import json

from agent_fakes import Harness, ScriptedClient, ScriptedDecisions, call, turn

from familiar.core.tasks import TaskStatus
from familiar.knowledge.completion_check import completion_question


def payload(request, index=-1):
    return json.loads(request["input"][index]["result"][0]["text"])


def test_question_includes_task_and_claim():
    question = completion_question("完成第一關", "已過關")
    assert question["type"] == "noul"
    assert "完成第一關" in question["instructions"] and "已過關" in question["instructions"]


async def test_confirmed_completion_succeeds():
    h = Harness(ScriptedClient(turn(call("task_done", summary="已送出"))), completion=ScriptedDecisions(0.9))
    result = await h.run()
    assert result.status is TaskStatus.SUCCEEDED and result.text == "已送出"
    assert h.steps[0].decision_calls == 1


async def test_false_claim_is_sent_back_then_accepted():
    h = Harness(ScriptedClient(turn(call("task_done", summary="過關了"), interaction_id="i1"),
                               turn(call("click", "c2", x=1, y=1), interaction_id="i2"),
                               turn(call("task_done", "c3", summary="真的過關了"), interaction_id="i3")),
                completion=ScriptedDecisions(0.2, 0.95))
    result = await h.run()
    assert result.status is TaskStatus.SUCCEEDED and result.text == "真的過關了"
    rejected = payload(h.client.requests[1])
    assert rejected["ok"] is False and "還沒完成" in rejected["error"]
    assert [s.ok for s in h.steps] == [False, True, True]


async def test_repeated_false_claims_fail_instead_of_lying():
    claims = [turn(call("task_done", f"c{i}", summary="完成第一關")) for i in range(3)]
    h = Harness(ScriptedClient(*claims), completion=ScriptedDecisions(0.1, 0.1, 0.1))
    result = await h.run()
    assert result.status is TaskStatus.FAILED
    assert "畫面檢查 3 次都判斷還沒完成" in result.error and "完成第一關" in result.error


async def test_text_answer_is_checked_too():
    h = Harness(ScriptedClient(turn(text="我玩完了"), turn(text="這次真的完成")),
                completion=ScriptedDecisions(0.2, 0.9))
    result = await h.run()
    assert result.status is TaskStatus.SUCCEEDED and result.text == "這次真的完成"
    second = h.client.requests[1]
    assert second["previous"] is None and "完成檢查" in second["input"][0]["text"]


async def test_checker_errors_do_not_block():
    h = Harness(ScriptedClient(turn(call("task_done", summary="好了"))),
                completion=ScriptedDecisions(RuntimeError("decisions down")))
    assert (await h.run()).status is TaskStatus.SUCCEEDED
