import dataclasses

import pytest

from familiar.core.coords import CoordinateError, to_normalized, to_pixel
from familiar.core.models import Action, ActionKind, Frame, Point, Risk, SafetyDecision


class TestFrame:
    def test_rejects_non_positive_size(self):
        with pytest.raises(ValueError):
            Frame(0, 800)
        with pytest.raises(ValueError):
            Frame(1280, -1)


class TestToPixel:
    frame = Frame(1280, 800)

    def test_center(self):
        assert to_pixel(500, 500, self.frame) == Point(640, 400)

    def test_origin(self):
        assert to_pixel(0, 0, self.frame) == Point(0, 0)

    def test_upper_bound_is_clamped_inside_frame(self):
        assert to_pixel(1000, 1000, self.frame) == Point(1279, 799)
        assert to_pixel(999, 999, self.frame) == Point(1279, 799)

    @pytest.mark.parametrize("x,y", [(-1, 0), (0, -5), (1001, 0), (0, 1500)])
    def test_out_of_range_raises(self, x, y):
        with pytest.raises(CoordinateError):
            to_pixel(x, y, self.frame)

    def test_round_trip(self):
        assert to_normalized(Point(640, 400), self.frame) == (500, 500)


class TestAction:
    def test_args_are_read_only(self):
        action = Action(ActionKind.CLICK, {"x": 1, "y": 2})
        with pytest.raises(TypeError):
            action.args["x"] = 5  # type: ignore[index]

    def test_lists_become_tuples(self):
        action = Action(ActionKind.PRESS_KEYS, {"keys": ["ctrl", "s"]})
        assert action.args["keys"] == ("ctrl", "s")

    def test_original_dict_is_not_shared(self):
        source = {"x": 1}
        action = Action(ActionKind.CLICK, source)
        source["x"] = 99
        assert action.args["x"] == 1

    def test_is_frozen(self):
        action = Action(ActionKind.WAIT, {})
        with pytest.raises(dataclasses.FrozenInstanceError):
            action.intent = "changed"  # type: ignore[misc]

    def test_defaults(self):
        action = Action(ActionKind.WAIT, {})
        assert action.risk is Risk.LOW
        assert action.safety is None


class TestSafetyDecision:
    def test_require_confirmation(self):
        assert SafetyDecision("require_confirmation", "付款").requires_confirmation
        assert not SafetyDecision("regular").requires_confirmation
