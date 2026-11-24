"""第 3 層：只用 Decisions API 決定下一步要點哪個元素（SRS FR-650～658、SDD 2.1）。不消耗代理人模型額度。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from familiar.executors.elements import Element, ElementTable
from familiar.gemini.decisions import DecisionClient, DecisionResult
from familiar.gemini.model_router import ModelRouter, Role
from familiar.vision.images import downscale_png
from familiar.vision.overlay import draw_marks

NONE = "none"
EST_TOKENS = 1500
# 適合直接點擊的元素；輸入框、下拉選單需要打字或選值，交給代理人模型（FR-655）
CLICKABLE_ROLES = frozenset({"button", "link", "checkbox", "radio", "tab", "menuitem", "option", "switch", "summary"})

RISK_QUESTION = {
    "type": "noul",
    "instructions": "如果點擊畫面中被黃色框標出的元素，會不會造成付款、購買、送出、刪除，或其他無法復原的結果？",
    "criteria": {
        "true_description": "會造成付款、購買、送出、刪除或其他不可復原的結果。",
        "false_description": "只是開啟頁面、切換分頁、關閉、返回、翻頁等可以復原的一般操作。",
    },
}


@dataclass(frozen=True)
class ActorConfig:
    enabled: bool = True
    mark_threshold: float = 0.8
    risk_threshold: float = 0.5
    max_candidates: int = 60
    stall_steps: int = 2
    screenshot_max_side: int = 1024


@dataclass(frozen=True)
class ActorDecision:
    element: Element | None
    confidence: float = 0.0
    risky: bool = False
    decision_calls: int = 0
    model: str | None = None
    reason: str = ""


def candidates_of(table: ElementTable | None, limit: int) -> tuple[Element, ...]:
    if not table:
        return ()
    usable = [e for e in table.elements if e.role in CLICKABLE_ROLES and not e.is_password and e.name]
    return tuple(usable[:limit])


def next_question(instruction: str, history: Sequence[str], candidates: Sequence[Element]) -> dict:
    done = "\n".join(f"- {line}" for line in history[-10:]) or "（還沒有）"
    criteria = {f"m{i}": f"黃色標記 {i}：{e.role}「{e.name}」" for i, e in enumerate(candidates, start=1)}
    criteria[NONE] = "下一步不是點擊這些元素（例如需要輸入文字、需要等待、需要捲動），或任務已經完成"
    return {
        "type": "choice",
        "instructions": f"任務：{instruction}\n已經做過的步驟：\n{done}\n"
                        "為了繼續完成任務，下一步應該點擊畫面上哪一個黃色編號標記的元素？",
        "criteria": criteria,
    }


class DecisionActor:
    def __init__(self, router: ModelRouter, client: DecisionClient, config: ActorConfig | None = None):
        self._router = router
        self._client = client
        self.config = config or ActorConfig()

    async def _ask(self, png: bytes, questions: dict) -> tuple[DecisionResult, str]:
        image = downscale_png(png, self.config.screenshot_max_side)
        routed = await self._router.call(
            Role.DECISION, lambda model: self._client.decide(model=model, image_png=image, questions=questions),
            est_tokens=EST_TOKENS, tokens_of=lambda result: result.tokens_in)
        return routed.value, routed.model

    async def choose(self, png: bytes, table: ElementTable | None, instruction: str,
                     history: Sequence[str]) -> ActorDecision:
        candidates = candidates_of(table, self.config.max_candidates)
        if not candidates:
            return ActorDecision(None, reason="沒有可點擊的候選")
        marked = draw_marks(png, [e.box for e in candidates])
        result, model = await self._ask(marked, {"next": next_question(instruction, history, candidates)})
        answer = result.answers.get("next")
        if not answer or answer.choice in (None, NONE) or answer.confidence < self.config.mark_threshold:
            confidence = answer.confidence if answer else 0.0
            return ActorDecision(None, confidence, decision_calls=1, model=model, reason="交給代理人模型")
        element = candidates[int(answer.choice[1:]) - 1]
        risk, _ = await self._ask(draw_marks(png, [element.box]), {"risky": RISK_QUESTION})
        risky_answer = risk.answers.get("risky")
        risky = bool(risky_answer and (risky_answer.probability or 0) >= self.config.risk_threshold)
        return ActorDecision(element, answer.confidence, risky, decision_calls=2, model=model)
