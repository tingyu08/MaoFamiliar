"""Gemini Decisions API（gemini-decision-1）的轉接層：送截圖＋問題，回傳各選項機率（SRS 4.2）。"""

from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol


@dataclass(frozen=True)
class Answer:
    kind: str                     # choice | noul | score
    choice: str | None = None
    probability: float | None = None   # noul：是的機率
    confidence: float = 0.0
    probabilities: Mapping[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class DecisionResult:
    answers: Mapping[str, Answer]
    tokens_in: int = 0


class DecisionClient(Protocol):
    async def decide(self, *, model: str, image_png: bytes,
                     questions: Mapping[str, Mapping[str, Any]]) -> DecisionResult: ...


def _answer(raw: Any) -> Answer:
    get = (lambda k: raw.get(k)) if isinstance(raw, Mapping) else (lambda k: getattr(raw, k, None))
    return Answer(kind=str(get("type") or ""), choice=get("choice"), probability=get("noul"),
                  confidence=float(get("confidence") or 0.0), probabilities=dict(get("probabilities") or {}))


def result_from_response(response: Any) -> DecisionResult:
    answers = getattr(response, "answers", None) or {}
    usage = getattr(response, "usage", None)
    return DecisionResult(answers={key: _answer(value) for key, value in answers.items()},
                          tokens_in=getattr(usage, "total_input_tokens", None) or 0)


class GeminiDecisionClient:
    """sdk_client 需以 http_options={"api_version": "v1beta"} 建立。"""

    def __init__(self, sdk_client: Any):
        self._sdk = sdk_client

    async def decide(self, *, model: str, image_png: bytes,
                     questions: Mapping[str, Mapping[str, Any]]) -> DecisionResult:
        image = {"type": "image", "mime_type": "image/png", "data": base64.b64encode(image_png).decode("ascii")}
        response = await asyncio.to_thread(self._sdk.decisions.create, model=model, input=[image],
                                           questions=dict(questions))
        return result_from_response(response)
