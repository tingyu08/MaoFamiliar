"""反射層：代理人模型定規則（慢），Decisions API 每輪看畫面選規則、程式立刻按鍵（快）。

規則是資料，不是程式：換遊戲不用改程式，代理人看懂畫面後自己寫規則（SRS FR-670～676）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from familiar.backends.action_args import REFLEX_UNKNOWN
from familiar.core.models import Action, ActionKind
from familiar.gemini.decisions import DecisionClient
from familiar.gemini.model_router import ModelRouter, Role
from familiar.vision.images import crop_png, downscale_png

EST_TOKENS = 600  # 512px 的遊戲畫面大約這麼多 token
UNKNOWN_DESCRIPTION = "畫面不符合以上任何一條規則（例如過場、選單、載入中，或看不清楚）"


@dataclass(frozen=True)
class ReflexConfig:
    enabled: bool = True
    max_side: int = 512          # 送給 Decisions 的遊戲畫面長邊；越小越快、越省 token
    threshold: float = 0.5       # 信心低於這個值視同 unknown
    max_unknown: int = 3         # 連續幾輪 unknown 就停下來交回代理人
    stall_ticks: int = 6         # 遊戲畫面連續幾輪沒變化就停下來
    max_iterations: int = 60


@dataclass(frozen=True)
class ReflexRule:
    id: str
    when: str
    action: str            # hold / tap / wait
    keys: tuple[str, ...]
    seconds: float
    times: int


@dataclass(frozen=True)
class ReflexPolicy:
    question: str
    rules: tuple[ReflexRule, ...]
    iterations: int

    def rule(self, rule_id: str) -> ReflexRule | None:
        return next((r for r in self.rules if r.id == rule_id), None)


@dataclass(frozen=True)
class ReflexChoice:
    rule: ReflexRule | None   # None：unknown 或信心不足
    confidence: float
    model: str


def policy_from_args(args: Mapping[str, Any], max_iterations: int) -> ReflexPolicy:
    """set_reflex 的參數已經過 SetReflexArgs 驗證，這裡只轉成不可變的規則物件。"""
    rules = tuple(
        ReflexRule(id=str(r["id"]), when=str(r["when"]), action=str(r["action"]),
                   keys=tuple(str(k) for k in r.get("keys", ())), seconds=float(r.get("seconds", 0.3)),
                   times=int(r.get("times", 1)))
        for r in args["rules"]
    )
    iterations = min(int(args.get("iterations", 20)), max_iterations)
    return ReflexPolicy(question=str(args["question"]), rules=rules, iterations=iterations)


def reflex_question(policy: ReflexPolicy) -> dict[str, Any]:
    criteria = {rule.id: rule.when for rule in policy.rules}
    return {
        "type": "choice",
        "instructions": f"這是遊戲畫面。{policy.question}\n選出最符合目前畫面的一項。",
        "criteria": {**criteria, REFLEX_UNKNOWN: UNKNOWN_DESCRIPTION},
    }


def rule_action(rule: ReflexRule) -> Action:
    intent = f"反射：{rule.id}"
    if rule.action == "hold":
        return Action(ActionKind.HOLD_KEYS, {"keys": list(rule.keys), "seconds": rule.seconds}, intent=intent)
    if rule.action == "tap":
        return Action(ActionKind.TAP_KEYS, {"keys": list(rule.keys), "times": rule.times}, intent=intent)
    return Action(ActionKind.WAIT, {"seconds": rule.seconds}, intent=intent)


def game_image(png: bytes, focus: Mapping[str, float] | None, max_side: int) -> bytes:
    """只送遊戲畫面那一塊，再縮小：Decisions 回應更快、token 更少（免費額度每分鐘 5 萬）。"""
    cropped = crop_png(png, dict(focus)) if focus else png
    return downscale_png(cropped, max_side)


class ReflexDecider:
    def __init__(self, router: ModelRouter, client: DecisionClient, config: ReflexConfig | None = None):
        self._router = router
        self._client = client
        self.config = config or ReflexConfig()

    async def choose(self, image_png: bytes, policy: ReflexPolicy) -> ReflexChoice:
        questions = {"reflex": reflex_question(policy)}
        routed = await self._router.call(
            Role.DECISION, lambda model: self._client.decide(model=model, image_png=image_png, questions=questions),
            est_tokens=EST_TOKENS, tokens_of=lambda result: result.tokens_in)
        answer = routed.value.answers.get("reflex")
        if not answer or answer.choice in (None, REFLEX_UNKNOWN) or answer.confidence < self.config.threshold:
            return ReflexChoice(None, answer.confidence if answer else 0.0, routed.model)
        return ReflexChoice(policy.rule(answer.choice), answer.confidence, routed.model)
