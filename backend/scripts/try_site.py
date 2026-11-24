"""在真實網站試跑一個任務（真的 Gemini＋Decisions），印出每一步由哪一層處理。

用法：
  uv run python scripts/try_site.py <網址> "<想做的事>" [--show] [--approve-risky]

- 預設 headless；加 --show 會開出 Chromium 視窗讓你看。
- 高風險動作的確認窗預設一律「拒絕」（真實網站上不要讓試跑腳本送出、購買或刪除任何東西）；
  只有加 --approve-risky 才會核准。
- 需要登入、驗證碼時會直接停止任務。
- 資料存在 backend/data/try-runs/（已在 .gitignore）。
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from dotenv import load_dotenv  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from familiar.api.app import create_app  # noqa: E402
from familiar.api.context import ServerSettings  # noqa: E402
from familiar.config import load_config  # noqa: E402
from familiar.core.agent_types import LoopConfig  # noqa: E402
from familiar.gemini.decisions import GeminiDecisionClient  # noqa: E402
from familiar.gemini.interactions import GeminiInteractionsClient  # noqa: E402
from familiar.main import build_services  # noqa: E402

TOKEN = "try-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
MAX_EVENTS = 5000  # 反射層每輪都會發步驟事件
LAYER = {"agent": "代理人", "actor": "第3層", "skill": "重播", "reflex": "反射"}


def clients():
    load_dotenv(BACKEND / ".env")
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        sys.exit("backend/.env 沒有 GEMINI_API_KEY")
    from google import genai

    options = {"timeout": 90_000, "retry_options": {"attempts": 1}}
    return (GeminiInteractionsClient(genai.Client(api_key=key, http_options=options)),
            GeminiDecisionClient(genai.Client(api_key=key, http_options={**options, "api_version": "v1beta"})))


def drive(client, ws, url: str, instruction: str, approve: bool, mode: str = "ui") -> tuple[str, list[str]]:
    log: list[str] = []
    response = client.post("/api/tasks", json={"url": url, "instruction": instruction, "mode": mode},
                           headers=AUTH).json()
    if not response["success"]:
        sys.exit(f"無法建立任務：{response['error']}")
    task_id = response["data"]["id"]
    for _ in range(MAX_EVENTS):
        event = ws.receive_json()
        kind = event["type"]
        if kind == "confirm_request":
            log.append(f"確認請求：{event['action']['intent']}（{event['reason']}）→ {'核准' if approve else '拒絕'}")
            ws.send_json({"type": "confirm_response", "request_id": event["request_id"], "approved": approve})
        elif kind == "user_help_request":
            log.append(f"模型求助：{event['reason']} → 停止任務")
            ws.send_json({"type": "stop", "task_id": task_id})
        elif kind == "waiting_quota":
            log.append(f"等待額度：{event['ready_at']}")
        elif kind == "quota_update":
            log.append(f"呼叫代理人模型：{event['model']}")
        elif kind == "step":
            print(f"  · 第 {event['index'] + 1} 步 [{LAYER.get(event['layer'], event['layer'])}] "
                  f"{event['action']['intent'] or event['action']['kind']} → {'成功' if event['ok'] else '失敗'}",
                  flush=True)
        elif kind == "task_finished":
            log.append(f"任務結束：{event['status']}")
            break
    return task_id, log


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("url")
    parser.add_argument("instruction")
    parser.add_argument("--show", action="store_true", help="顯示 Chromium 視窗")
    parser.add_argument("--approve-risky", action="store_true", help="核准高風險動作（預設拒絕）")
    parser.add_argument("--max-steps", type=int, default=25, help="步數上限（避免燒光免費額度，預設 25）")
    parser.add_argument("--game", action="store_true", help="遊戲模式：可以按住按鍵，卡住偵測只看遊戲畫面")
    args = parser.parse_args()

    agent, decisions = clients()
    data_dir = BACKEND / "data" / "try-runs" / datetime.now().strftime("%Y%m%d-%H%M%S")
    services = build_services(load_config(BACKEND / "config.yaml"), data_dir, agent, headless=not args.show,
                              decisions=decisions, loop_config=LoopConfig(max_steps=args.max_steps))
    settings = ServerSettings(token=TOKEN, allowed_hosts=("testserver",))
    started = time.time()
    print(f"任務：{args.instruction}\n網址：{args.url}\n", flush=True)
    with TestClient(create_app(services, settings, on_shutdown=services.close)) as client:
        with client.websocket_connect("/ws") as ws:
            ws.send_json({"type": "auth", "token": TOKEN})
            ws.receive_json()
            task_id, log = drive(client, ws, args.url, args.instruction, args.approve_risky,
                                 "realtime" if args.game else "ui")
        detail = client.get(f"/api/tasks/{task_id}", headers=AUTH).json()["data"]

    task, steps = detail["task"], detail["steps"]
    print(f"\n狀態：{task['status']}　耗時：{time.time() - started:.1f} 秒　步數：{task['steps_count']}　"
          f"代理人呼叫：{task['agent_calls']}　第3層：{task['actor_steps']} 步　Decisions：{task['decision_calls']} 次")
    print(f"結果：{task['result_text']}\n錯誤：{task['error_message']}")
    print("事件：\n  " + "\n  ".join(log))
    print("步驟：")
    for s in steps:
        verdict = "成功" if s["result"]["ok"] else f"失敗：{s['result']['message']}"
        if s["result"]["ok"] and s["action"]["kind"] == "set_reflex":
            verdict = f"成功：{s['result']['message']}"
        print(f"  {s['idx']} [{LAYER.get(s['layer'], s['layer'])}|{s['model']}] {s['action']['kind']} "
              f"{ {k: v for k, v in s['action']['args'].items() if k != 'summary'} } "
              f"risk={s['action'].get('risk')} → {verdict}（{s['duration_ms']} ms）")
    usage = {m: q["day_requests"] for m, q in services.router.snapshot().items() if q["day_requests"]}
    print(f"本次額度用量：{usage}\n截圖與紀錄：{data_dir}")


if __name__ == "__main__":
    main()
