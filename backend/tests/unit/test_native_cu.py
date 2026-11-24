import pytest

from familiar.backends.native_cu import UNSUPPORTED_NATIVE, NativeCuBackend
from familiar.core.models import Action, ActionKind, CallError, Environment, Frame, OperatingMode

FRAME = Frame(1280, 800)


def call(name: str, **arguments):
    return {"id": "n1", "name": name, "arguments": arguments}


@pytest.fixture
def backend():
    return NativeCuBackend()


class TestTools:
    def test_computer_use_tool_config(self, backend):
        tools = backend.tools(Environment.DESKTOP, OperatingMode.UI)
        [cu] = [t for t in tools if t["type"] == "computer_use"]
        assert cu["environment"] == "desktop"
        assert cu["enable_prompt_injection_detection"] is True
        assert set(UNSUPPORTED_NATIVE) <= set(cu["excluded_predefined_functions"])

    def test_adds_custom_flow_tools_but_not_custom_pointer_tools(self, backend):
        names = {t.get("name") for t in backend.tools(Environment.BROWSER, OperatingMode.UI)}
        assert {"ask_user", "task_done", "click_element"} <= names
        assert "click" not in names and "type_text" not in names

    def test_realtime_mode_is_not_supported(self, backend):
        with pytest.raises(ValueError):
            backend.tools(Environment.DESKTOP, OperatingMode.REALTIME)


class TestParse:
    def test_click_with_safety_decision(self, backend):
        [action] = backend.parse(
            [call("click", x=500, y=500, intent="購買",
                  safety_decision={"decision": "require_confirmation", "explanation": "會付款"})],
            FRAME,
        )
        assert action.kind is ActionKind.CLICK
        assert (action.args["x"], action.args["y"]) == (640, 400)
        assert action.safety is not None and action.safety.requires_confirmation
        assert action.safety.explanation == "會付款"

    def test_type(self, backend):
        [action] = backend.parse([call("type", text="你好", press_enter=True, intent="輸入")], FRAME)
        assert action.kind is ActionKind.TYPE_TEXT
        assert action.args["text"] == "你好" and action.args["press_enter"] is True

    def test_hotkey_and_press_key(self, backend):
        [hotkey, press] = backend.parse(
            [call("hotkey", keys=["Control", "c"], intent="複製"), call("press_key", key="Enter", intent="確認")],
            FRAME,
        )
        assert hotkey.kind is ActionKind.PRESS_KEYS and hotkey.args["keys"] == ("control", "c")
        assert press.args["keys"] == ("enter",)

    def test_drag_and_drop(self, backend):
        [action] = backend.parse(
            [call("drag_and_drop", start_x=0, start_y=0, end_x=500, end_y=500, intent="拖")], FRAME)
        assert action.kind is ActionKind.DRAG
        assert dict(action.args) == {"x1": 0, "y1": 0, "x2": 640, "y2": 400}

    def test_scroll_magnitude_to_notches(self, backend):
        [action] = backend.parse(
            [call("scroll", x=500, y=500, direction="down", magnitude_in_pixels=300, intent="捲")], FRAME)
        assert action.kind is ActionKind.SCROLL
        assert action.args["amount"] == 3 and action.args["direction"] == "down"

    def test_take_screenshot_becomes_zero_wait(self, backend):
        [action] = backend.parse([call("take_screenshot", intent="看")], FRAME)
        assert action.kind is ActionKind.WAIT and action.args["seconds"] == 0

    def test_unsupported_native_function(self, backend):
        [result] = backend.parse([call("triple_click", x=1, y=1, intent="x")], FRAME)
        assert isinstance(result, CallError)

    def test_custom_function_is_delegated(self, backend):
        [action] = backend.parse([call("ask_user", reason="登入", intent="求助")], FRAME)
        assert isinstance(action, Action) and action.kind is ActionKind.ASK_USER

    def test_invalid_native_args(self, backend):
        [result] = backend.parse([call("click", x=-3, y=1, intent="x")], FRAME)
        assert isinstance(result, CallError)
