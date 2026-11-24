"""Gemini Interactions API 的薄轉接層（代理人模型）。SDK 是同步的，放到執行緒裡跑。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Protocol, Sequence


@dataclass(frozen=True)
class AgentTurn:
    interaction_id: str
    function_calls: tuple[dict[str, Any], ...]
    text: str
    tokens_in: int = 0
    tokens_out: int = 0


class AgentModelClient(Protocol):
    async def create_turn(
        self,
        *,
        model: str,
        system_instruction: str,
        input: Sequence[dict[str, Any]],
        tools: Sequence[dict[str, Any]],
        previous_interaction_id: str | None,
    ) -> AgentTurn: ...


def turn_from_interaction(interaction: Any) -> AgentTurn:
    calls = tuple(
        {"id": step.id, "name": step.name, "arguments": dict(step.arguments or {})}
        for step in (getattr(interaction, "steps", None) or [])
        if getattr(step, "type", None) == "function_call"
    )
    usage = getattr(interaction, "usage", None)
    return AgentTurn(
        interaction_id=getattr(interaction, "id", None) or "",
        function_calls=calls,
        text=getattr(interaction, "output_text", None) or "",
        tokens_in=getattr(usage, "total_input_tokens", None) or 0,
        tokens_out=getattr(usage, "total_output_tokens", None) or 0,
    )


class GeminiInteractionsClient:
    def __init__(self, sdk_client: Any):
        self._sdk = sdk_client

    async def create_turn(
        self,
        *,
        model: str,
        system_instruction: str,
        input: Sequence[dict[str, Any]],
        tools: Sequence[dict[str, Any]],
        previous_interaction_id: str | None,
    ) -> AgentTurn:
        kwargs: dict[str, Any] = {"model": model, "system_instruction": system_instruction,
                                  "input": list(input), "tools": list(tools)}
        if previous_interaction_id:
            kwargs["previous_interaction_id"] = previous_interaction_id
        interaction = await asyncio.to_thread(self._sdk.interactions.create, **kwargs)
        return turn_from_interaction(interaction)
