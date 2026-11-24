"""完成檢查（SRS FR-605）：模型說「完成了」之前，先用 Decisions API 看畫面確認，避免謊報成功。"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from familiar.gemini.decisions import DecisionClient
from familiar.gemini.model_router import ModelRouter, Role
from familiar.vision.images import downscale_png

logger = logging.getLogger(__name__)

EST_TOKENS = 1500
MAX_SUMMARY_CHARS = 500


@dataclass(frozen=True)
class CompletionConfig:
    enabled: bool = True
    threshold: float = 0.5     # 「已完成」的機率低於這個值就退回給模型
    max_rejections: int = 2    # 退回幾次後仍回報完成 → 任務判定失敗（不謊報成功）
    screenshot_max_side: int = 1024


@dataclass(frozen=True)
class CompletionVerdict:
    done: bool
    probability: float | None = None   # None：沒有檢查（停用或 Decisions 出錯）
    decision_calls: int = 0


def completion_question(instruction: str, summary: str) -> dict:
    return {
        "type": "noul",
        "instructions": (f"任務：{instruction}\nAI 回報的結果：{summary[:MAX_SUMMARY_CHARS] or '（沒有說明）'}\n"
                         "AI 的說法可能不正確，或只完成了其中一部分（例如只進入遊戲、只打開頁面）。"
                         "不要相信說法，只根據這張畫面判斷：任務的「最終目標」是否真的已經達成？"),
        "criteria": {
            "true_description": "畫面清楚顯示任務的最終目標已經達成，例如出現要找的資訊、送出成功的訊息、過關或完成畫面。",
            "false_description": "畫面顯示任務還在進行中、只完成前面的步驟、還沒開始、失敗，或從畫面看不出最終目標已經達成。",
        },
    }


class CompletionChecker:
    def __init__(self, router: ModelRouter, client: DecisionClient, config: CompletionConfig | None = None):
        self._router = router
        self._client = client
        self.config = config or CompletionConfig()

    async def verify(self, png: bytes, instruction: str, summary: str) -> CompletionVerdict:
        if not self.config.enabled:
            return CompletionVerdict(done=True)
        image = downscale_png(png, self.config.screenshot_max_side)
        question = {"done": completion_question(instruction, summary)}
        try:
            routed = await self._router.call(
                Role.DECISION, lambda model: self._client.decide(model=model, image_png=image, questions=question),
                est_tokens=EST_TOKENS, tokens_of=lambda result: result.tokens_in)
        except Exception as error:  # noqa: BLE001 — 檢查本身失敗時不擋住任務，但記錄下來
            logger.warning("完成檢查失敗，略過檢查：%s", error)
            return CompletionVerdict(done=True)
        answer = routed.value.answers.get("done")
        probability = answer.probability if answer and answer.probability is not None else 0.0
        return CompletionVerdict(done=probability >= self.config.threshold, probability=probability, decision_calls=1)
