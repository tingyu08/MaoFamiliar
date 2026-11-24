"""診斷：為什麼回傳 function_result 一直立刻 503。

先做一次截圖＋自訂函式呼叫拿到 function_call，再用幾種格式回傳結果，看哪一種會成功。
"""

from __future__ import annotations

import argparse
import json
from typing import Any

from actions import CUSTOM_TOOLS, SYSTEM_INSTRUCTION
from common import FIXTURES_DIR, function_calls_of, image_block, make_clients, pause, timed_call

SCENE = FIXTURES_DIR / "click" / "scene_00.png"
NEXT_SCENE = FIXTURES_DIR / "click" / "scene_01.png"
PROMPT = {"type": "text", "text": "請點擊畫面上寫著「郵件」的按鈕。"}


def first_call(client: Any, model: str, store: bool | None) -> tuple[Any, dict[str, Any]] | None:
    kwargs: dict[str, Any] = {"model": model, "system_instruction": SYSTEM_INSTRUCTION,
                              "input": [PROMPT, image_block(SCENE)], "tools": list(CUSTOM_TOOLS)}
    if store is not None:
        kwargs["store"] = store
    outcome = timed_call(lambda: client.interactions.create(**kwargs), f"{model} 第一輪 store={store}", retries=1)
    calls = function_calls_of(outcome.value) if outcome.ok else []
    return (outcome.value, calls[0]) if calls else None


def result_step(call: dict[str, Any], result: Any) -> dict[str, Any]:
    return {"type": "function_result", "name": call["name"], "call_id": call["id"], "result": result}


VARIANTS = {
    "string": lambda: json.dumps({"status": "clicked"}),
    "text_list": lambda: [{"type": "text", "text": json.dumps({"status": "clicked"})}],
    "text_image_list": lambda: [{"type": "text", "text": "clicked"}, image_block(NEXT_SCENE)],
}


def try_variant(client: Any, model: str, name: str, store: bool | None, separate_image: bool) -> None:
    first = first_call(client, model, store)
    if first is None:
        print(f"   {name}: 第一輪沒拿到 function_call，跳過")
        return
    interaction, call = first
    inputs: list[dict[str, Any]] = [result_step(call, VARIANTS[name]())]
    if separate_image:
        inputs = [*inputs, image_block(NEXT_SCENE)]
    kwargs: dict[str, Any] = {"model": model, "system_instruction": SYSTEM_INSTRUCTION,
                              "previous_interaction_id": interaction.id, "input": inputs,
                              "tools": list(CUSTOM_TOOLS)}
    if store is not None:
        kwargs["store"] = store
    label = f"{model} 回傳 {name}{' +獨立圖片' if separate_image else ''} store={store}"
    outcome = timed_call(lambda: client.interactions.create(**kwargs), label, retries=1)
    if outcome.ok:
        print(f"   ✔ 成功，下一步：{function_calls_of(outcome.value) or outcome.value.output_text}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="gemini-3.6-flash")
    parser.add_argument("--delay", type=float, default=4.0)
    args = parser.parse_args()
    client, _ = make_clients()
    cases = [
        ("string", None, False),
        ("text_list", None, False),
        ("text_image_list", None, False),
        ("text_list", None, True),
        ("string", True, False),
    ]
    for name, store, separate in cases:
        try_variant(client, args.model, name, store, separate)
        pause(args.delay)


if __name__ == "__main__":
    main()
