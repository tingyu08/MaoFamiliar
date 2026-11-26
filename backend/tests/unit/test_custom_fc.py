import json

import pytest

from familiar.backends.action_schema import companion_tools
from familiar.backends.custom_fc import CustomFcBackend
from familiar.core.models import (
    Action,
    ActionKind,
    ActionOutcome,
    CallError,
    Environment,
    Frame,
    OperatingMode,
    Risk,
)

FRAME = Frame(1280, 800)


def call(name: str, call_id: str = "c1", **arguments):
    return {"id": call_id, "name": name, "arguments": arguments}


@pytest.fixture
def backend():
    return CustomFcBackend()


class TestTools:
    def names(self, backend, env, mode):
        return {tool["name"] for tool in backend.tools(env, mode)}

    def test_browser_tools(self, backend):
        names = self.names(backend, Environment.BROWSER, OperatingMode.UI)
        assert {"click", "type_text", "navigate", "click_element", "ask_user", "task_done"} <= names
        assert "hold_keys" not in names

    def test_realtime_tools(self, backend):
        names = self.names(backend, Environment.DESKTOP, OperatingMode.REALTIME)
        assert {"hold_keys", "look", "hold_mouse", "tap_keys", "click"} <= names
        assert "navigate" not in names

    def test_desktop_ui_tools(self, backend):
        names = self.names(backend, Environment.DESKTOP, OperatingMode.UI)
        assert "request_mode" in names
        assert "hold_keys" not in names and "navigate" not in names

    def test_zoom_is_offered_everywhere(self, backend):
        for env, mode in [(Environment.BROWSER, OperatingMode.UI), (Environment.BROWSER, OperatingMode.REALTIME),
                          (Environment.DESKTOP, OperatingMode.UI), (Environment.DESKTOP, OperatingMode.REALTIME)]:
            assert "zoom" in self.names(backend, env, mode)
        assert "zoom" in {t["name"] for t in companion_tools(Environment.BROWSER)}  # 原生 computer_use 也能用

    def test_every_tool_is_a_valid_declaration(self, backend):
        for tool in backend.tools(Environment.DESKTOP, OperatingMode.REALTIME):
            assert tool["type"] == "function"
            assert tool["description"]
            params = tool["parameters"]
            assert params["type"] == "object"
            assert "intent" in params["required"]


class TestParse:
    def test_click_converts_to_pixels(self, backend):
        [action] = backend.parse([call("click", x=500, y=500, intent="點擊郵件", risk="high")], FRAME)
        assert isinstance(action, Action)
        assert action.kind is ActionKind.CLICK
        assert (action.args["x"], action.args["y"]) == (640, 400)
        assert action.intent == "點擊郵件"
        assert action.risk is Risk.HIGH
        assert action.call_id == "c1"

    def test_drag_converts_all_points(self, backend):
        [action] = backend.parse([call("drag", x1=0, y1=0, x2=500, y2=500, intent="拖曳")], FRAME)
        assert dict(action.args) == {"x1": 0, "y1": 0, "x2": 640, "y2": 400}

    def test_out_of_range_coordinate_is_an_error(self, backend):
        [result] = backend.parse([call("click", x=1500, y=10, intent="x")], FRAME)
        assert isinstance(result, CallError)
        assert result.call_id == "c1"
        assert "x" in result.message

    def test_missing_argument_is_an_error(self, backend):
        [result] = backend.parse([call("click", x=10, intent="x")], FRAME)
        assert isinstance(result, CallError)
        assert "y" in result.message

    def test_unknown_function_is_an_error(self, backend):
        [result] = backend.parse([call("teleport", intent="x")], FRAME)
        assert isinstance(result, CallError)
        assert "teleport" in result.message

    def test_hold_longer_than_limit_is_an_error(self, backend):
        [result] = backend.parse([call("hold_keys", keys=["w"], seconds=6, intent="走")], FRAME)
        assert isinstance(result, CallError)

    def test_navigate_rejects_non_http(self, backend):
        [result] = backend.parse([call("navigate", url="javascript:alert(1)", intent="x")], FRAME)
        assert isinstance(result, CallError)

    def test_navigate_accepts_https(self, backend):
        [action] = backend.parse([call("navigate", url="https://example.com/a", intent="開網頁")], FRAME)
        assert action.args["url"] == "https://example.com/a"

    def test_keys_are_normalized_to_lowercase_tuple(self, backend):
        [action] = backend.parse([call("press_keys", keys=["Ctrl", "S"], intent="存檔")], FRAME)
        assert action.args["keys"] == ("ctrl", "s")

    def test_order_preserved_and_errors_isolated(self, backend):
        results = backend.parse(
            [
                call("click", "a", x=1, y=1, intent="1"),
                call("click", "b", x=9999, y=1, intent="2"),
                call("task_done", "c", summary="完成", intent="結束"),
            ],
            FRAME,
        )
        assert [type(r) for r in results] == [Action, CallError, Action]
        assert [r.call_id for r in results] == ["a", "b", "c"]

    def test_flow_actions(self, backend):
        [ask] = backend.parse([call("ask_user", reason="需要登入", intent="求助")], FRAME)
        assert ask.kind is ActionKind.ASK_USER
        assert ask.args["reason"] == "需要登入"

    def test_click_element_ref_format(self, backend):
        [ok] = backend.parse([call("click_element", ref="e12", intent="點")], FRAME)
        assert ok.args["ref"] == "e12"
        [bad] = backend.parse([call("click_element", ref="button", intent="點")], FRAME)
        assert isinstance(bad, CallError)

    def test_zoom_region_converts_to_pixels(self, backend):
        [action] = backend.parse([call("zoom", x1=0, y1=0, x2=500, y2=500, intent="看星等")], FRAME)
        assert action.kind is ActionKind.ZOOM
        assert dict(action.args) == {"x1": 0, "y1": 0, "x2": 640, "y2": 400}

    def test_zoom_rejects_inverted_or_tiny_region(self, backend):
        inverted, tiny = backend.parse([call("zoom", "a", x1=500, y1=0, x2=100, y2=500, intent="x"),
                                        call("zoom", "b", x1=100, y1=100, x2=105, y2=400, intent="x")], FRAME)
        assert isinstance(inverted, CallError) and isinstance(tiny, CallError)

    def test_extra_arguments_are_ignored(self, backend):
        [action] = backend.parse([call("wait", seconds=1, intent="等", foo="bar")], FRAME)
        assert "foo" not in action.args


class TestBuildResults:
    def test_success_with_screenshot(self, backend):
        outcome = ActionOutcome(call_id="c1", name="click", ok=True, data={"url": "https://a.b"},
                                screenshot_png=b"\x89PNG")
        [step] = backend.build_results([outcome])
        assert step["type"] == "function_result"
        assert step["call_id"] == "c1" and step["name"] == "click"
        assert "is_error" not in step
        text, image = step["result"]
        assert json.loads(text["text"]) == {"ok": True, "url": "https://a.b"}
        assert image["type"] == "image" and image["mime_type"] == "image/png"

    def test_error_without_screenshot(self, backend):
        outcome = ActionOutcome(call_id="c2", name="click", ok=False, message="找不到元素")
        [step] = backend.build_results([outcome])
        assert step["is_error"] is True
        assert len(step["result"]) == 1
        assert json.loads(step["result"][0]["text"])["error"] == "找不到元素"

    def test_zoom_image_comes_before_the_screenshot(self, backend):
        outcome = ActionOutcome(call_id="z", name="zoom", ok=True, message="第一張是放大圖",
                                zoom_png=b"ZOOM", screenshot_png=b"SHOT")
        [step] = backend.build_results([outcome])
        text, zoom, shot = step["result"]
        assert json.loads(text["text"])["message"] == "第一張是放大圖"
        assert [zoom["data"], shot["data"]] == ["Wk9PTQ==", "U0hPVA=="]  # base64 的 ZOOM、SHOT

    def test_safety_acknowledgement_is_included(self, backend):
        outcome = ActionOutcome(call_id="c3", name="click", ok=True, safety_acknowledged=True)
        [step] = backend.build_results([outcome])
        assert json.loads(step["result"][0]["text"])["safety_acknowledgement"] is True
