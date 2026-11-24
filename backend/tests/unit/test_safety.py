import pytest

from familiar.core.models import Action, ActionKind, Environment, Risk, SafetyDecision
from familiar.core.safety import SafetyConfig, SafetyContext, Verdict, evaluate

BROWSER = SafetyContext(environment=Environment.BROWSER)
CONFIG = SafetyConfig(blocked_domains=("bank.example",), blocked_windows=("1Password",))


def act(kind, risk=Risk.LOW, safety=None, **args):
    return Action(kind, args, intent="t", risk=risk, safety=safety)


def test_plain_click_is_allowed():
    assert evaluate(act(ActionKind.CLICK, x=1, y=1), BROWSER, CONFIG).verdict is Verdict.ALLOW


def test_high_risk_requires_confirmation():
    result = evaluate(act(ActionKind.CLICK, Risk.HIGH, x=1, y=1), BROWSER, CONFIG)
    assert result.verdict is Verdict.CONFIRM


def test_native_safety_decision_requires_confirmation():
    decision = SafetyDecision("require_confirmation", "付款")
    result = evaluate(act(ActionKind.CLICK, safety=decision, x=1, y=1), BROWSER, CONFIG)
    assert result.verdict is Verdict.CONFIRM
    assert "付款" in result.reason


@pytest.mark.parametrize("url", ["https://bank.example/login", "https://www.bank.example"])
def test_blocked_domain(url):
    result = evaluate(act(ActionKind.NAVIGATE, url=url), BROWSER, CONFIG)
    assert result.verdict is Verdict.BLOCK


def test_similar_domain_is_not_blocked():
    result = evaluate(act(ActionKind.NAVIGATE, url="https://notbank.example"), BROWSER, CONFIG)
    assert result.verdict is Verdict.ALLOW


def test_current_page_on_blocked_domain_blocks_everything():
    context = SafetyContext(environment=Environment.BROWSER, current_url="https://bank.example/x")
    assert evaluate(act(ActionKind.CLICK, x=1, y=1), context, CONFIG).verdict is Verdict.BLOCK


def test_non_http_navigation_blocked():
    result = evaluate(act(ActionKind.NAVIGATE, url="file:///c:/x"), BROWSER, CONFIG)
    assert result.verdict is Verdict.BLOCK


@pytest.mark.parametrize("kind,args", [
    (ActionKind.TYPE_TEXT, {"text": "secret"}),
    (ActionKind.FILL_ELEMENT, {"ref": "e1", "text": "secret"}),
])
def test_typing_into_password_is_blocked(kind, args):
    context = SafetyContext(environment=Environment.BROWSER, target_is_password=True)
    result = evaluate(Action(kind, args), context, CONFIG)
    assert result.verdict is Verdict.BLOCK
    assert result.reason == "password"


def test_blocked_window():
    context = SafetyContext(environment=Environment.DESKTOP, window_title="1Password - Vault")
    assert evaluate(act(ActionKind.CLICK, x=1, y=1), context, CONFIG).verdict is Verdict.BLOCK


def test_non_impactful_actions_skip_risk():
    result = evaluate(act(ActionKind.WAIT, Risk.HIGH, seconds=1), BROWSER, CONFIG)
    assert result.verdict is Verdict.ALLOW
