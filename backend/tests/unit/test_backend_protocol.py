import pytest

from familiar.backends.base import ActionBackend
from familiar.backends.custom_fc import CustomFcBackend
from familiar.backends.native_cu import NativeCuBackend
from familiar.core.models import Environment, OperatingMode


@pytest.mark.parametrize("backend", [CustomFcBackend(), NativeCuBackend()])
def test_backends_satisfy_protocol(backend):
    assert isinstance(backend, ActionBackend)


def test_browser_realtime_is_rejected():
    with pytest.raises(ValueError):
        CustomFcBackend().tools(Environment.BROWSER, OperatingMode.REALTIME)


def test_sdk_style_call_objects_are_accepted():
    class Step:
        id = "s1"
        name = "wait"
        arguments = {"seconds": 2, "intent": "等"}

    from familiar.core.models import Frame

    [action] = CustomFcBackend().parse([Step()], Frame(100, 100))
    assert action.args["seconds"] == 2 and action.call_id == "s1"
