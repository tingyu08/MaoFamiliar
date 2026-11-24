"""反射層：規則驗證、Decisions 問題組裝、按鍵對應，以及接進代理人迴圈後的行為。"""

from __future__ import annotations

import io
import json
from dataclasses import replace
from pathlib import Path

from PIL import Image

from familiar.backends.action_schema import custom_tools
from familiar.backends.custom_fc import CustomFcBackend
from familiar.config import load_config, to_reflex_config
from familiar.core.agent_types import LoopConfig
from familiar.core.models import Action, ActionKind, CallError, Environment, Frame, OperatingMode
from familiar.core.tasks import TaskStatus
from familiar.knowledge.reflex import (
    ReflexConfig,
    game_image,
    policy_from_args,
    reflex_question,
    rule_action,
)

from agent_fakes import SPEC, FakeExecutor, Harness, ScriptedClient, ScriptedDecisions, call, frame_png, turn

GAME_SPEC = replace(SPEC, instruction="打倒敵人", mode=OperatingMode.REALTIME)
RULES = [
    {"id": "up", "when": "敵人在玩家上方", "action": "hold", "keys": ["ArrowUp"], "seconds": 0.3},
    {"id": "attack", "when": "敵人就在玩家旁邊", "action": "tap", "keys": ["a"], "times": 3},
    {"id": "idle", "when": "畫面上沒有敵人", "action": "wait", "seconds": 0.5},
]


def reflex_call(call_id: str = "r1", iterations: int = 3, rules=None) -> dict:
    return call("set_reflex", call_id, question="敵人和玩家的相對位置？", rules=rules or RULES,
                iterations=iterations)


def parse(arguments: dict) -> Action | CallError:
    raw = {"id": "c1", "name": "set_reflex", "arguments": {"intent": "交給反射層", **arguments}}
    return CustomFcBackend().parse([raw], Frame(320, 200))[0]


# ── 規則驗證 ─────────────────────────────────────────────


def test_valid_rules_are_parsed_and_frozen():
    action = parse({"question": "q", "rules": RULES, "iterations": 10})
    assert isinstance(action, Action)
    policy = policy_from_args(action.args, max_iterations=60)
    assert [r.id for r in policy.rules] == ["up", "attack", "idle"]
    assert policy.rules[0].keys == ("arrowup",)  # 按鍵名稱統一小寫
    assert policy.iterations == 10


def test_iterations_are_capped_by_config():
    action = parse({"question": "q", "rules": RULES, "iterations": 50})
    assert policy_from_args(action.args, max_iterations=8).iterations == 8


def test_invalid_rules_are_reported_to_the_model():
    bad_cases = [
        {"question": "q", "rules": RULES[:1]},                                            # 少於 2 條
        {"question": "q", "rules": [RULES[0], {**RULES[1], "id": "up"}]},                  # 代號重複
        {"question": "q", "rules": [RULES[0], {**RULES[1], "id": "unknown"}]},             # 保留字
        {"question": "q", "rules": [RULES[0], {"id": "x", "when": "w", "action": "hold"}]},  # hold 沒有 keys
        {"question": "q", "rules": [RULES[0], {**RULES[0], "id": "slow", "seconds": 3}]},  # 超過 2 秒
        {"question": "q", "rules": [RULES[0], {**RULES[1], "id": "Bad-Id"}]},              # 代號格式
        {"question": "q", "rules": RULES, "iterations": 0},
    ]
    for arguments in bad_cases:
        assert isinstance(parse(arguments), CallError), arguments


def test_question_lists_every_rule_plus_unknown():
    policy = policy_from_args(parse({"question": "相對位置？", "rules": RULES}).args, 60)
    question = reflex_question(policy)
    assert question["type"] == "choice"
    assert "相對位置？" in question["instructions"]
    assert list(question["criteria"]) == ["up", "attack", "idle", "unknown"]
    assert question["criteria"]["up"] == "敵人在玩家上方"


def test_rules_map_to_key_actions():
    policy = policy_from_args(parse({"question": "q", "rules": RULES}).args, 60)
    hold, tap, wait = (rule_action(rule) for rule in policy.rules)
    assert (hold.kind, dict(hold.args)) == (ActionKind.HOLD_KEYS, {"keys": ("arrowup",), "seconds": 0.3})
    assert (tap.kind, tap.args["times"]) == (ActionKind.TAP_KEYS, 3)
    assert (wait.kind, wait.args["seconds"]) == (ActionKind.WAIT, 0.5)


def test_game_image_is_cropped_to_focus_and_downscaled():
    png = frame_png(1)  # 320×200
    cropped = Image.open(io.BytesIO(game_image(png, {"x1": 0, "y1": 0, "x2": 160, "y2": 100}, 512)))
    assert cropped.size == (160, 100)
    small = Image.open(io.BytesIO(game_image(png, None, 100)))
    assert max(small.size) == 100


def test_tool_is_offered_only_in_game_mode():
    def names(mode):
        return {t["name"] for t in custom_tools(Environment.BROWSER, mode)}

    assert "set_reflex" in names(OperatingMode.REALTIME)
    assert "set_reflex" not in names(OperatingMode.UI)


def test_config_block_is_loaded():
    config = load_config(Path(__file__).resolve().parents[2] / "config.yaml")
    reflex = to_reflex_config(config)
    assert reflex.enabled and reflex.max_side == 512 and reflex.max_iterations == 60


# ── 接進代理人迴圈 ─────────────────────────────────────────


async def test_reflex_runs_rules_without_calling_the_agent():
    client = ScriptedClient(turn(reflex_call(iterations=3)), turn(call("task_done", "d1", summary="打倒了")))
    decisions = ScriptedDecisions(("attack", 0.9), ("up", 0.8), ("attack", 0.95))
    harness = Harness(client, reflex=decisions)

    result = await harness.run(GAME_SPEC)

    assert result.status is TaskStatus.SUCCEEDED
    assert len(client.requests) == 2  # 反射的 3 輪沒有呼叫代理人模型
    assert [a.kind for a in harness.executor.executed] == [ActionKind.TAP_KEYS, ActionKind.HOLD_KEYS,
                                                           ActionKind.TAP_KEYS]
    assert [(s.index, s.layer) for s in harness.steps] == [(0, "reflex"), (1, "reflex"), (2, "reflex"),
                                                           (3, "agent"), (4, "agent")]
    assert all(s.model == "decision-1" and s.decision_calls == 1 for s in harness.steps[:3])
    assert result.steps == 5
    report = str(client.requests[1]["input"])
    assert "反射層跑了 3 輪" in report and "attack 2 次" in report and "up 1 次" in report
    # 步驟會存進資料庫、送給前端：巢狀的規則也要能轉成 JSON
    saved = json.loads(json.dumps(harness.steps[3].action, ensure_ascii=False))
    assert saved["args"]["rules"][1]["keys"] == ["a"]
    for event in harness.sink.events:
        if event["type"] == "step":
            json.dumps(event, ensure_ascii=False)


async def test_reflex_sends_only_the_game_area():
    client = ScriptedClient(turn(reflex_call(iterations=1)), turn(call("task_done", "d1", summary="完成")))
    decisions = ScriptedDecisions(("idle", 0.9))
    executor = FakeExecutor()
    executor.focus = {"x1": 0, "y1": 0, "x2": 160, "y2": 100}
    harness = Harness(client, executor=executor, reflex=decisions)

    await harness.run(GAME_SPEC)

    sent = Image.open(io.BytesIO(decisions.requests[0]["png"]))
    assert sent.size == (160, 100)


async def test_repeated_unknown_hands_control_back():
    client = ScriptedClient(turn(reflex_call(iterations=10)), turn(call("task_done", "d1", summary="完成")))
    decisions = ScriptedDecisions(("unknown", 0.9), ("attack", 0.3), ("unknown", 0.99))  # 0.3 低於門檻
    harness = Harness(client, reflex=decisions, reflex_config=ReflexConfig(max_unknown=3))

    result = await harness.run(GAME_SPEC)

    assert result.status is TaskStatus.SUCCEEDED
    assert harness.executor.executed == []
    reflex_step = harness.steps[0]
    assert reflex_step.action["kind"] == "set_reflex" and reflex_step.decision_calls == 3
    assert "連續 3 輪沒有規則符合" in str(client.requests[1]["input"])


async def test_static_game_screen_stops_the_reflex():
    client = ScriptedClient(turn(reflex_call(iterations=10)), turn(call("task_done", "d1", summary="完成")))
    decisions = ScriptedDecisions(*[("attack", 0.9)] * 10)
    harness = Harness(client, executor=FakeExecutor(static_screen=True), reflex=decisions,
                      reflex_config=ReflexConfig(stall_ticks=2))

    await harness.run(GAME_SPEC)

    assert len(harness.executor.executed) == 1
    assert "連續 2 輪沒有變化" in str(client.requests[1]["input"])


async def test_decisions_error_returns_to_the_agent():
    client = ScriptedClient(turn(reflex_call()), turn(call("task_done", "d1", summary="完成")))
    harness = Harness(client, reflex=ScriptedDecisions(("attack", 0.9), RuntimeError("壞掉")))

    result = await harness.run(GAME_SPEC)

    assert result.status is TaskStatus.SUCCEEDED
    assert len(harness.executor.executed) == 1
    assert "Decisions 呼叫失敗" in str(client.requests[1]["input"])


async def test_stop_interrupts_the_reflex():
    client = ScriptedClient(turn(reflex_call(iterations=20)))
    holder: dict = {}

    def on_event(event):
        if event["type"] == "step" and event.get("layer") == "reflex":
            holder["harness"].control.request_stop()

    harness = Harness(client, reflex=ScriptedDecisions(*[("attack", 0.9)] * 20), on_event=on_event)
    holder["harness"] = harness

    result = await harness.run(GAME_SPEC)

    assert result.status is TaskStatus.STOPPED
    assert len(harness.executor.executed) == 1


async def test_reflex_steps_do_not_count_toward_max_steps():
    client = ScriptedClient(turn(reflex_call(iterations=5)), turn(call("task_done", "d1", summary="完成")))
    harness = Harness(client, reflex=ScriptedDecisions(*[("attack", 0.9)] * 5), config=LoopConfig(max_steps=3))

    result = await harness.run(GAME_SPEC)

    assert result.status is TaskStatus.SUCCEEDED
    assert result.steps == 7


async def test_failed_key_press_stops_the_reflex():
    client = ScriptedClient(turn(reflex_call(iterations=5)), turn(call("task_done", "d1", summary="完成")))
    harness = Harness(client, executor=FakeExecutor(failing_kinds=frozenset({"tap_keys"})),
                      reflex=ScriptedDecisions(*[("attack", 0.9)] * 5))

    await harness.run(GAME_SPEC)

    assert len(harness.executor.executed) == 1
    assert not harness.steps[0].ok
    assert "按鍵失敗" in str(client.requests[1]["input"])


async def test_disabled_reflex_tells_the_model_to_use_keys():
    client = ScriptedClient(turn(reflex_call()), turn(call("task_done", "d1", summary="完成")))
    harness = Harness(client)

    await harness.run(GAME_SPEC)

    assert harness.steps[0].ok is False
    assert "反射層未啟用" in harness.steps[0].message
