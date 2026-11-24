"""驗證 4：只用 Decisions API 決定「下一步點哪裡」，不呼叫代理人模型。

四種做法：
  text_literal  候選清單是文字框（模擬 OCR），問題直接說按鈕文字（基準）
  text_goal     同上，但問題只描述目標，不說按鈕文字（測推理）
  som_goal      在截圖上畫編號標記，選項是編號（不依賴文字，可用於圖示）
  grid_goal     兩段式格子：先在 12×8 格選一格，再放大附近區域用 6×6 格選一次
"""

from __future__ import annotations

import argparse
import base64
import io
import json
from statistics import mean
from typing import Any

from PIL import Image

from actions import inside
from common import FIXTURES_DIR, image_block, make_clients, new_meta, pause, save_results, timed_call
from overlay import Cell, draw_grid, draw_marks, neighborhood

DECISION_MODEL = "gemini-decision-1"
NONE_OPTION = "none"
STAGE1_GRID = (12, 8)
STAGE2_GRID = (6, 6)
STAGE2_SCALE = 3


def png_block(img: Image.Image) -> dict[str, str]:
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    return {"type": "image", "data": base64.b64encode(buffer.getvalue()).decode("ascii"), "mime_type": "image/png"}


def region_word(box: dict[str, int], width: int, height: int) -> str:
    cx, cy = (box["x1"] + box["x2"]) / 2, (box["y1"] + box["y2"]) / 2
    col = "左" if cx < width / 3 else "右" if cx > width * 2 / 3 else "中"
    row = "上" if cy < height / 3 else "下" if cy > height * 2 / 3 else "中"
    return f"{row}{col}方"


def decide(client: Any, image: dict[str, str], instructions: str, criteria: dict[str, str], label: str) -> dict:
    outcome = timed_call(lambda: client.decisions.create(
        model=DECISION_MODEL,
        input=[image],
        questions={"next": {"type": "choice", "instructions": instructions, "criteria": criteria}},
    ), label)
    if not outcome.ok:
        return {"ok": False, "error": outcome.error_message, "latency_ms": outcome.latency_ms}
    answer = outcome.value.answers["next"]
    return {"ok": True, "choice": answer.choice, "confidence": round(answer.confidence, 3),
            "latency_ms": outcome.latency_ms}


def run_text(client: Any, scene: dict, image: Image.Image, use_goal: bool) -> dict:
    criteria = {f"c{i}": f"按鈕，文字「{el['label']}」，位於畫面{region_word(el['box'], scene['width'], scene['height'])}"
                for i, el in enumerate(scene["elements"])}
    criteria[NONE_OPTION] = "沒有任何候選能達成目標"
    target = next(i for i, el in enumerate(scene["elements"]) if el["label"] == scene["target_label"])
    ask = (f"目標：{scene['target_goal']}。為了達成目標，下一步應該點擊哪一個候選？" if use_goal
           else f"應該點擊寫著「{scene['target_label']}」的按鈕。是哪一個候選？")
    result = decide(client, png_block(image), ask, criteria, f"{'text_goal' if use_goal else 'text_literal'} {scene['file']}")
    return {**result, "correct": result.get("choice") == f"c{target}"}


def run_som(client: Any, scene: dict, image: Image.Image) -> dict:
    boxes = [el["box"] for el in scene["elements"]]
    marked = draw_marks(image, boxes)
    criteria = {f"m{i}": f"黃色標記 {i} 的元素" for i in range(1, len(boxes) + 1)}
    criteria[NONE_OPTION] = "沒有任何標記的元素能達成目標"
    target = next(i for i, el in enumerate(scene["elements"], start=1) if el["label"] == scene["target_label"])
    ask = f"目標：{scene['target_goal']}。為了達成目標，下一步應該點擊哪一個黃色編號標記的元素？"
    result = decide(client, png_block(marked), ask, criteria, f"som_goal {scene['file']}")
    return {**result, "correct": result.get("choice") == f"m{target}"}


def run_grid(client: Any, scene: dict, image: Image.Image) -> dict:
    ask = f"目標：{scene['target_goal']}。為了達成目標，應該點擊的元素中心位於哪一個格子？"
    gridded, cells = draw_grid(image, *STAGE1_GRID)
    stage1 = decide(client, png_block(gridded), ask, {c.cell_id: f"格子 {c.cell_id}" for c in cells},
                    f"grid1 {scene['file']}")
    if not stage1["ok"]:
        return {**stage1, "correct": False}
    chosen = next(c for c in cells if c.cell_id == stage1["choice"])
    x1, y1, x2, y2 = neighborhood(chosen, cells, image.width, image.height)
    crop = image.crop((x1, y1, x2, y2)).resize(((x2 - x1) * STAGE2_SCALE, (y2 - y1) * STAGE2_SCALE))
    zoomed, sub_cells = draw_grid(crop, *STAGE2_GRID, lowercase=True)
    stage2 = decide(client, png_block(zoomed), ask, {c.cell_id: f"格子 {c.cell_id}" for c in sub_cells},
                    f"grid2 {scene['file']}")
    if not stage2["ok"]:
        return {**stage2, "stage1": stage1["choice"], "correct": False}
    sub = next(c for c in sub_cells if c.cell_id == stage2["choice"])
    px, py = x1 + sub.center[0] / STAGE2_SCALE, y1 + sub.center[1] / STAGE2_SCALE
    return {"ok": True, "stage1": stage1["choice"], "stage2": stage2["choice"],
            "confidence": round(min(stage1["confidence"], stage2["confidence"]), 3),
            "latency_ms": stage1["latency_ms"] + stage2["latency_ms"],
            "pixel": [int(px), int(py)], "correct": inside(int(px), int(py), scene["target_box"])}


def load(kind: str) -> list[dict]:
    return json.loads((FIXTURES_DIR / kind / "labels.json").read_text(encoding="utf-8"))


def summarize(rows: list[dict]) -> list[dict]:
    out = []
    for key in sorted({(r["mode"], r["set"]) for r in rows}):
        mine = [r for r in rows if (r["mode"], r["set"]) == key]
        ok = [r for r in mine if r["ok"]]
        right = [r["confidence"] for r in ok if r["correct"]]
        wrong = [r["confidence"] for r in ok if not r["correct"]]
        out.append({"mode": key[0], "set": key[1], "n": len(mine), "api_errors": len(mine) - len(ok),
                    "accuracy": round(len(right) / len(ok), 2) if ok else None,
                    "conf_correct": round(mean(right), 2) if right else None,
                    "conf_wrong": round(mean(wrong), 2) if wrong else None,
                    "mean_ms": int(mean(r["latency_ms"] for r in ok)) if ok else None})
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--delay", type=float, default=0.5)
    parser.add_argument("--modes", nargs="*", help="只跑指定做法，例如 grid_goal")
    parser.add_argument("--files", nargs="*", help="只跑指定畫面檔名")
    args = parser.parse_args()
    _, client = make_clients()
    rows: list[dict] = []
    plans = [("click", ("text_literal", "text_goal", "som_goal", "grid_goal")),
             ("icons", ("som_goal", "grid_goal"))]
    for kind, all_modes in plans:
        modes = [m for m in all_modes if not args.modes or m in args.modes]
        for scene in load(kind):
            if args.files and scene["file"] not in args.files:
                continue
            image = Image.open(FIXTURES_DIR / kind / scene["file"]).convert("RGB")
            for mode in modes:
                if mode.startswith("text"):
                    result = run_text(client, scene, image, use_goal=mode == "text_goal")
                elif mode == "som_goal":
                    result = run_som(client, scene, image)
                else:
                    result = run_grid(client, scene, image)
                rows.append({"set": kind, "mode": mode, "file": scene["file"], "target": scene["target_label"],
                             "goal": scene["target_goal"], **result})
                pause(args.delay)

    summary = summarize(rows)
    path = save_results("spike04-decision-driven", new_meta("spike_04"), [*summary, *rows])
    print(f"\n{'做法':<14}{'畫面':<8}{'數量':>4}{'正確率':>8}{'答對信心':>9}{'答錯信心':>9}{'平均ms':>8}")
    for s in summary:
        print(f"{s['mode']:<14}{s['set']:<8}{s['n']:>4}{str(s['accuracy']):>8}{str(s['conf_correct']):>9}"
              f"{str(s['conf_wrong']):>9}{str(s['mean_ms']):>8}")
    print("\n答錯：")
    for r in rows:
        if r["ok"] and not r["correct"]:
            print(f"  {r['mode']} {r['file']} 目標「{r['target']}」得到 {r.get('choice') or r.get('pixel')}（信心 {r['confidence']}）")
    print(f"\n完整結果：{path.resolve()}")


if __name__ == "__main__":
    main()
