"""驗證 3：Decisions API（gemini-decision-1）用截圖判斷畫面。

(a) 畫面狀態分類：choice 問題，選項為已知狀態 + unknown
(b) 高風險動作判斷：noul 問題，紅圈標出的按鈕按下去是否不可復原
"""

from __future__ import annotations

import argparse
import json
from statistics import mean
from typing import Any

from common import (
    DECISION_MODEL,
    FIXTURES_DIR,
    image_block,
    make_clients,
    new_meta,
    pause,
    save_results,
    timed_call,
)

STATE_CRITERIA = {
    "main_menu": "遊戲主選單：有遊戲標題與開始遊戲、任務、商店、設定等直排按鈕，沒有彈窗遮擋。",
    "reward_popup": "獎勵彈窗：背景變暗，畫面中央有顯示獲得獎勵與領取按鈕的面板。",
    "battle": "戰鬥中：有血條、敵人、技能按鈕與回合數。",
    "loading": "載入畫面：幾乎全暗，有進度條或載入中文字。",
    "shop": "商店：多個商品格子並標示價格。",
    "settings": "設定頁：音量、畫質等選項與滑桿。",
    "unknown": "以上皆不符合。",
}

RISK_QUESTION = {
    "type": "noul",
    "instructions": "如果點擊畫面中被紅色圓圈標出的按鈕，會不會造成付款、送出、刪除，或其他無法復原的結果？",
    "criteria": {
        "true_description": "會造成付款、購買、送出、刪除或其他不可復原的結果。",
        "false_description": "只是關閉、返回、翻頁等可以復原的一般操作。",
    },
}


def answer_dict(answer: Any) -> dict[str, Any]:
    dump = answer.model_dump() if hasattr(answer, "model_dump") else dict(answer)
    return {k: dump.get(k) for k in ("type", "choice", "noul", "confidence", "probabilities") if k in dump}


def classify_states(client: Any, delay: float) -> list[dict[str, Any]]:
    labels = json.loads((FIXTURES_DIR / "states" / "labels.json").read_text(encoding="utf-8"))
    question = {"screen": {"type": "choice", "instructions": "這張遊戲截圖目前是哪一個畫面？",
                           "criteria": STATE_CRITERIA}}
    rows = []
    for item in labels:
        outcome = timed_call(lambda item=item: client.decisions.create(
            model=DECISION_MODEL,
            input=[image_block(FIXTURES_DIR / "states" / item["file"])],
            questions=question,
        ), f"狀態 {item['file']}")
        row: dict[str, Any] = {"task": "state", "file": item["file"], "expected": item["state"],
                               "ok": outcome.ok, "latency_ms": outcome.latency_ms, "error": outcome.error_message}
        if outcome.ok:
            ans = answer_dict(outcome.value.answers["screen"])
            row = {**row, **ans, "correct": ans.get("choice") == item["state"]}
        rows.append(row)
        pause(delay)
    return rows


def judge_risk(client: Any, delay: float) -> list[dict[str, Any]]:
    labels = json.loads((FIXTURES_DIR / "risk" / "labels.json").read_text(encoding="utf-8"))
    rows = []
    for item in labels:
        outcome = timed_call(lambda item=item: client.decisions.create(
            model=DECISION_MODEL,
            input=[image_block(FIXTURES_DIR / "risk" / item["file"])],
            questions={"risky": RISK_QUESTION},
        ), f"風險 {item['file']}")
        row: dict[str, Any] = {"task": "risk", "file": item["file"], "label": item["label"],
                               "expected": item["high_risk"], "ok": outcome.ok,
                               "latency_ms": outcome.latency_ms, "error": outcome.error_message}
        if outcome.ok:
            ans = answer_dict(outcome.value.answers["risky"])
            row = {**row, **ans, "correct": (ans.get("noul", 0) >= 0.5) == item["high_risk"]}
        rows.append(row)
        pause(delay)
    return rows


def summary_of(rows: list[dict[str, Any]], task: str) -> dict[str, Any]:
    mine = [r for r in rows if r["task"] == task]
    ok = [r for r in mine if r["ok"]]
    return {
        "task": task,
        "total": len(mine),
        "api_errors": len(mine) - len(ok),
        "accuracy": round(sum(1 for r in ok if r.get("correct")) / len(ok), 2) if ok else None,
        "mean_confidence": round(mean(r["confidence"] for r in ok), 2) if ok else None,
        "mean_latency_ms": int(mean(r["latency_ms"] for r in ok)) if ok else None,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--delay", type=float, default=1.0, help="Decisions 免費版 200 RPM，可以較短")
    args = parser.parse_args()

    _, client = make_clients()
    rows = [*classify_states(client, args.delay), *judge_risk(client, args.delay)]
    summaries = [summary_of(rows, "state"), summary_of(rows, "risk")]
    path = save_results("spike03-decisions", new_meta("spike_03"), [*summaries, *rows])

    for s in summaries:
        print(s)
    print("\n錯判：")
    for r in rows:
        if r["ok"] and not r.get("correct"):
            got = r.get("choice", r.get("noul"))
            print(f"  {r['file']}: 預期 {r['expected']}，得到 {got}（信心 {r.get('confidence')}）")
    print(f"\n完整結果：{path.resolve()}")


if __name__ == "__main__":
    main()
