"""驗證 1：每個模型在目前 API key（免費版）下能用哪些能力。

每個模型測：
  A 純文字
  B 截圖 + 自訂函式（custom_fc）
  C 把 function_result（含新截圖）回傳，接續對話
  D 原生 computer_use，environment=desktop
  E 原生 computer_use，environment=browser
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from actions import CUSTOM_TOOLS, SYSTEM_INSTRUCTION
from common import (
    AGENT_MODELS,
    DEFAULT_DELAY_SEC,
    FIXTURES_DIR,
    CallOutcome,
    function_calls_of,
    image_block,
    make_clients,
    new_meta,
    pause,
    save_results,
    timed_call,
    usage_of,
)

SCENE = FIXTURES_DIR / "click" / "scene_00.png"
NEXT_SCENE = FIXTURES_DIR / "click" / "scene_01.png"


def target_label() -> str:
    labels = json.loads((FIXTURES_DIR / "click" / "labels.json").read_text(encoding="utf-8"))
    return labels[0]["target_label"]


def summarize(test: str, model: str, outcome: CallOutcome) -> dict[str, Any]:
    row: dict[str, Any] = {
        "model": model,
        "test": test,
        "ok": outcome.ok,
        "latency_ms": outcome.latency_ms,
        "error_type": outcome.error_type,
        "http_status": outcome.http_status,
        "error": outcome.error_message,
        "attempts": outcome.attempts,
    }
    if outcome.ok:
        interaction = outcome.value
        row["interaction_id"] = getattr(interaction, "id", None)
        row["function_calls"] = function_calls_of(interaction)
        row["output_text"] = (getattr(interaction, "output_text", None) or "")[:300]
        row["usage"] = usage_of(interaction)
    return row


def run_model(client: Any, model: str, delay: float, skip_browser: bool) -> list[dict[str, Any]]:
    label = target_label()
    prompt = {"type": "text", "text": f"請點擊畫面上寫著「{label}」的按鈕。"}
    rows: list[dict[str, Any]] = []

    text_only = timed_call(lambda: client.interactions.create(model=model, input="只回覆兩個字：OK"),
                           f"{model} A 純文字")
    rows.append(summarize("A_text_only", model, text_only))
    pause(delay)

    custom = timed_call(lambda: client.interactions.create(
        model=model,
        system_instruction=SYSTEM_INSTRUCTION,
        input=[prompt, image_block(SCENE)],
        tools=list(CUSTOM_TOOLS),
    ), f"{model} B 截圖+自訂函式")
    rows.append(summarize("B_image_custom_fc", model, custom))
    pause(delay)

    rows.append(run_roundtrip(client, model, custom))
    pause(delay)

    for env in ("desktop",) if skip_browser else ("desktop", "browser"):
        native = timed_call(lambda env=env: client.interactions.create(
            model=model,
            input=[prompt, image_block(SCENE)],
            tools=[{"type": "computer_use", "environment": env, "enable_prompt_injection_detection": True}],
        ), f"{model} 原生 computer_use ({env})")
        rows.append(summarize(f"{'D' if env == 'desktop' else 'E'}_native_cu_{env}", model, native))
        pause(delay)
    return rows


def run_roundtrip(client: Any, model: str, previous: CallOutcome) -> dict[str, Any]:
    calls = function_calls_of(previous.value) if previous.ok else []
    if not calls:
        return {"model": model, "test": "C_function_result_roundtrip", "ok": False,
                "error": "B 沒有回傳 function_call，無法測試回傳結果"}
    call = calls[0]
    result_input = [{
        "type": "function_result",
        "name": call["name"],
        "call_id": call["id"],
        "result": [
            {"type": "text", "text": json.dumps({"status": "clicked"}, ensure_ascii=False)},
            image_block(NEXT_SCENE),
        ],
    }]
    outcome = timed_call(lambda: client.interactions.create(
        model=model,
        system_instruction=SYSTEM_INSTRUCTION,
        previous_interaction_id=previous.value.id,
        input=result_input,
        tools=list(CUSTOM_TOOLS),
    ), f"{model} C 回傳函式結果")
    return summarize("C_function_result_roundtrip", model, outcome)


def print_table(rows: list[dict[str, Any]]) -> None:
    print(f"\n{'model':<22}{'test':<30}{'ok':<6}{'ms':>7}  detail")
    for r in rows:
        detail = r.get("error") or ", ".join(c["name"] for c in r.get("function_calls", [])) or r.get("output_text", "")
        print(f"{r['model']:<22}{r['test']:<30}{str(r['ok']):<6}{r.get('latency_ms', 0):>7}  {str(detail)[:90]}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--models", nargs="*", default=list(AGENT_MODELS))
    parser.add_argument("--delay", type=float, default=DEFAULT_DELAY_SEC)
    parser.add_argument("--skip-browser", action="store_true")
    args = parser.parse_args()

    client, _ = make_clients()
    rows = [row for model in args.models for row in run_model(client, model, args.delay, args.skip_browser)]
    path = save_results("spike01-capabilities", new_meta("spike_01", models=args.models), rows)
    print_table(rows)
    print(f"\n完整結果：{Path(path).resolve()}")


if __name__ == "__main__":
    main()
