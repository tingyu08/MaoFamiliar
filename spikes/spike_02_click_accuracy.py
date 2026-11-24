"""驗證 2：看截圖點按鈕的座標準不準。

對每個模型、每張 click 測試畫面，請模型點擊指定按鈕，計算：
- 命中率（換算後的像素是否落在按鈕框內）
- 與按鈕中心的平均像素誤差
- 延遲與 token

--backend custom（預設）：自訂 click 函式
--backend native：原生 computer_use（environment=desktop），讀取回傳的 x、y
"""

from __future__ import annotations

import argparse
import json
from statistics import mean
from typing import Any

from actions import CUSTOM_TOOLS, SYSTEM_INSTRUCTION, distance_to_center, inside, to_pixels
from common import (
    AGENT_MODELS,
    DEFAULT_DELAY_SEC,
    FIXTURES_DIR,
    function_calls_of,
    image_block,
    make_clients,
    new_meta,
    pause,
    save_results,
    timed_call,
    usage_of,
)

CLICK_DIR = FIXTURES_DIR / "click"


def tools_for(backend: str) -> list[dict[str, Any]]:
    if backend == "native":
        return [{"type": "computer_use", "environment": "desktop"}]
    return list(CUSTOM_TOOLS)


def first_xy(calls: list[dict[str, Any]]) -> tuple[float, float] | None:
    for call in calls:
        args = call["arguments"]
        if "x" in args and "y" in args:
            return float(args["x"]), float(args["y"])
    return None


def evaluate(scene: dict[str, Any], calls: list[dict[str, Any]]) -> dict[str, Any]:
    xy = first_xy(calls)
    if xy is None:
        return {"hit": False, "pixel": None, "error_px": None}
    px, py = to_pixels(xy[0], xy[1], scene["width"], scene["height"])
    box = scene["target_box"]
    return {"hit": inside(px, py, box), "pixel": [px, py], "error_px": round(distance_to_center(px, py, box), 1)}


def run_scene(client: Any, model: str, backend: str, scene: dict[str, Any]) -> dict[str, Any]:
    prompt = {"type": "text", "text": f"請點擊畫面上寫著「{scene['target_label']}」的按鈕。"}
    kwargs: dict[str, Any] = {
        "model": model,
        "input": [prompt, image_block(CLICK_DIR / scene["file"])],
        "tools": tools_for(backend),
    }
    if backend == "custom":
        kwargs["system_instruction"] = SYSTEM_INSTRUCTION
    outcome = timed_call(lambda: client.interactions.create(**kwargs), f"{model} {scene['file']}")
    row: dict[str, Any] = {"model": model, "backend": backend, "file": scene["file"],
                           "target": scene["target_label"], "ok": outcome.ok,
                           "latency_ms": outcome.latency_ms, "error": outcome.error_message}
    if not outcome.ok:
        return row
    calls = function_calls_of(outcome.value)
    return {**row, "calls": calls, "usage": usage_of(outcome.value), **evaluate(scene, calls)}


def summarize(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for model in sorted({r["model"] for r in rows}):
        mine = [r for r in rows if r["model"] == model]
        answered = [r for r in mine if r.get("pixel")]
        errors = [r["error_px"] for r in answered]
        out.append({
            "model": model,
            "scenes": len(mine),
            "api_errors": sum(1 for r in mine if not r["ok"]),
            "hit_rate": round(sum(1 for r in mine if r.get("hit")) / len(mine), 2) if mine else 0,
            "mean_error_px": round(mean(errors), 1) if errors else None,
            "mean_latency_ms": int(mean(r["latency_ms"] for r in mine)) if mine else None,
        })
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--models", nargs="*", default=list(AGENT_MODELS))
    parser.add_argument("--backend", choices=("custom", "native"), default="custom")
    parser.add_argument("--limit", type=int, default=8, help="每個模型測幾張畫面")
    parser.add_argument("--delay", type=float, default=DEFAULT_DELAY_SEC)
    args = parser.parse_args()

    scenes = json.loads((CLICK_DIR / "labels.json").read_text(encoding="utf-8"))[: args.limit]
    client, _ = make_clients()
    rows: list[dict[str, Any]] = []
    for model in args.models:
        for scene in scenes:
            rows.append(run_scene(client, model, args.backend, scene))
            pause(args.delay)

    summary = summarize(rows)
    path = save_results(f"spike02-click-{args.backend}", new_meta("spike_02", backend=args.backend), [*summary, *rows])
    print(f"\n{'model':<22}{'命中率':>8}{'平均誤差px':>12}{'平均ms':>10}{'API錯誤':>9}")
    for s in summary:
        print(f"{s['model']:<22}{s['hit_rate']:>8}{str(s['mean_error_px']):>12}{str(s['mean_latency_ms']):>10}{s['api_errors']:>9}")
    print(f"\n完整結果：{path.resolve()}")


if __name__ == "__main__":
    main()
