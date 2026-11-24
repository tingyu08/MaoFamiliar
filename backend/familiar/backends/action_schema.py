"""custom_fc 的函式宣告（給模型看的動作清單，SDD 4.2）。"""

from __future__ import annotations

from typing import Any

from familiar.backends.action_args import (
    MAX_ACTION_SECONDS,
    MAX_REFLEX_ITERATIONS,
    MAX_REFLEX_SECONDS,
    MAX_WAIT_SECONDS,
)
from familiar.core.models import ActionKind, Environment, OperatingMode

Tool = dict[str, Any]

_INTENT = {"type": "string", "description": "這一步要做什麼（給使用者看的簡短說明）"}
_RISK = {"type": "string", "enum": ["low", "high"],
         "description": "high：可能造成付款、送出、刪除或其他無法復原的結果"}
_COORD = {"type": "integer", "description": "0–999 的正規化座標"}
_KEYS = {"type": "array", "items": {"type": "string"}, "description": "按鍵名稱，例如 [\"ctrl\", \"s\"]"}


def _tool(kind: ActionKind, description: str, props: dict[str, Any], required: list[str],
          impactful: bool = False) -> Tool:
    properties = {**props, "intent": _INTENT, **({"risk": _RISK} if impactful else {})}
    return {
        "type": "function",
        "name": kind.value,
        "description": description,
        "parameters": {"type": "object", "properties": properties, "required": [*required, "intent"]},
    }


def _point(kind: ActionKind, description: str) -> Tool:
    return _tool(kind, description, {"x": _COORD, "y": _COORD}, ["x", "y"], impactful=True)


POINTER: tuple[Tool, ...] = (
    _point(ActionKind.CLICK, "左鍵點擊畫面上的一點"),
    _point(ActionKind.DOUBLE_CLICK, "左鍵雙擊"),
    _point(ActionKind.RIGHT_CLICK, "右鍵點擊"),
    _tool(ActionKind.DRAG, "從 (x1, y1) 拖曳到 (x2, y2)",
          {"x1": _COORD, "y1": _COORD, "x2": _COORD, "y2": _COORD}, ["x1", "y1", "x2", "y2"], impactful=True),
    _tool(ActionKind.SCROLL, "在 (x, y) 位置捲動",
          {"x": _COORD, "y": _COORD, "direction": {"type": "string", "enum": ["up", "down", "left", "right"]},
           "amount": {"type": "integer", "description": "捲動格數 1–10，預設 3"}},
          ["x", "y", "direction"]),
)

KEYBOARD: tuple[Tool, ...] = (
    _tool(ActionKind.TYPE_TEXT, "在目前焦點輸入文字。絕對不要輸入密碼，遇到密碼欄位請呼叫 ask_user",
          {"text": {"type": "string"}, "press_enter": {"type": "boolean"}}, ["text"], impactful=True),
    _tool(ActionKind.PRESS_KEYS, "同時按下再放開這些按鍵（快捷鍵或單一按鍵）", {"keys": _KEYS}, ["keys"], impactful=True),
)

REALTIME: tuple[Tool, ...] = (
    _tool(ActionKind.HOLD_KEYS, f"同時按住這些按鍵一段時間（最多 {MAX_ACTION_SECONDS:g} 秒），例如按住 w 前進",
          {"keys": _KEYS, "seconds": {"type": "number"}}, ["keys", "seconds"]),
    _tool(ActionKind.LOOK, "轉動視角：dx 正值向右、dy 正值向下，單位為度",
          {"dx": {"type": "number"}, "dy": {"type": "number"}}, ["dx", "dy"]),
    _tool(ActionKind.HOLD_MOUSE, f"按住滑鼠鍵一段時間（最多 {MAX_ACTION_SECONDS:g} 秒），例如挖掘",
          {"button": {"type": "string", "enum": ["left", "right", "middle"]}, "seconds": {"type": "number"}},
          ["seconds"]),
    _tool(ActionKind.TAP_KEYS, "連續點按這些按鍵數次",
          {"keys": _KEYS, "times": {"type": "integer"}}, ["keys"]),
    _tool(ActionKind.SET_REFLEX,
          "把需要即時反應的操作交給反射層（例如對齊敵人、攻擊、閃躲）。反射層每輪約 1 秒："
          "看一次遊戲畫面 → 依各規則的 when 描述選出最符合的一條 → 立刻按對應的鍵，重複 iterations 輪，"
          "最後把每條規則用了幾次、為什麼停下來回報給你。你再依結果調整規則或改做別的。",
          {"question": {"type": "string", "description": "每輪要判斷的問題，例如「玩家角色和最近的敵人相對位置是？」"},
           "rules": {"type": "array", "description": f"2–12 條規則；hold／wait 每次最多 {MAX_REFLEX_SECONDS:g} 秒",
                     "items": {"type": "object", "properties": {
                         "id": {"type": "string", "description": "英文小寫代號，例如 enemy_above"},
                         "when": {"type": "string", "description": "什麼畫面情況用這條規則（具體描述看得到的東西）"},
                         "action": {"type": "string", "enum": ["hold", "tap", "wait"]},
                         "keys": _KEYS,
                         "seconds": {"type": "number", "description": "hold／wait 的秒數"},
                         "times": {"type": "integer", "description": "tap 的次數 1–5"}},
                         "required": ["id", "when", "action"]}},
           "iterations": {"type": "integer", "description": f"要跑幾輪，1–{MAX_REFLEX_ITERATIONS}，預設 20"}},
          ["question", "rules"]),
)

BROWSER: tuple[Tool, ...] = (
    _tool(ActionKind.NAVIGATE, "前往網址（只接受 http/https）", {"url": {"type": "string"}}, ["url"], impactful=True),
    _tool(ActionKind.GO_BACK, "瀏覽器上一頁", {}, []),
)

ELEMENTS: tuple[Tool, ...] = (
    _tool(ActionKind.CLICK_ELEMENT, "點擊元素清單中的元素（優先使用，比座標準確）",
          {"ref": {"type": "string", "description": "元素編號，例如 e12"}}, ["ref"], impactful=True),
    _tool(ActionKind.FILL_ELEMENT, "在元素清單中的輸入框填入文字",
          {"ref": {"type": "string"}, "text": {"type": "string"}}, ["ref", "text"], impactful=True),
)

WAIT: tuple[Tool, ...] = (
    _tool(ActionKind.WAIT, f"等待畫面變化（最多 {MAX_WAIT_SECONDS:g} 秒）", {"seconds": {"type": "number"}}, []),
)

FLOW: tuple[Tool, ...] = (
    _tool(ActionKind.ASK_USER, "需要使用者協助時呼叫（登入、驗證碼、資訊不足、不確定是否安全）",
          {"reason": {"type": "string"}}, ["reason"]),
    _tool(ActionKind.TASK_DONE, "任務完成時呼叫，summary 寫給使用者看的結果", {"summary": {"type": "string"}}, ["summary"]),
)

PLANNING: tuple[Tool, ...] = (
    _tool(ActionKind.COMPLETE_SUBGOAL, "標記子目標完成",
          {"id": {"type": "string"}, "summary": {"type": "string"}}, ["id"]),
    _tool(ActionKind.REVISE_PLAN, "重新擬定子目標清單",
          {"subgoals": {"type": "array", "items": {"type": "string"}}}, ["subgoals"]),
)

KNOWLEDGE: tuple[Tool, ...] = (
    _tool(ActionKind.REGISTER_STATE, "目前畫面是值得記住的狀態時呼叫（例如主選單、獎勵彈窗）",
          {"name": {"type": "string"}, "description": {"type": "string"}}, ["name", "description"]),
    _tool(ActionKind.SAVE_NOTE, "記下對之後有幫助的操作心得", {"text": {"type": "string"}}, ["text"]),
    _tool(ActionKind.USE_SKILL, "執行已學會的技能", {"name": {"type": "string"}}, ["name"]),
)

REQUEST_MODE: tuple[Tool, ...] = (
    _tool(ActionKind.REQUEST_MODE, "建議切換操作模式（ui 或 realtime），需要使用者同意",
          {"mode": {"type": "string", "enum": ["ui", "realtime"]}, "reason": {"type": "string"}}, ["mode"]),
)

_COMMON = (*FLOW, *PLANNING, *KNOWLEDGE)


def custom_tools(env: Environment, mode: OperatingMode) -> tuple[Tool, ...]:
    """custom_fc 後端在此環境、模式下提供給模型的全部動作。"""
    base = (*POINTER, *KEYBOARD, *WAIT, *_COMMON)
    if env is Environment.BROWSER:
        # 網頁遊戲的即時模式：按住按鍵、連按、長按滑鼠（轉視角 look 需要指標鎖定，網頁不提供）
        extra = tuple(t for t in REALTIME if t["name"] != ActionKind.LOOK.value) if mode is OperatingMode.REALTIME else ()
        return (*base, *BROWSER, *ELEMENTS, *extra)
    extra = REALTIME if mode is OperatingMode.REALTIME else ()
    return (*base, *extra, *REQUEST_MODE)


def companion_tools(env: Environment) -> tuple[Tool, ...]:
    """搭配原生 computer_use 的自訂函式（原生沒有的流程、知識、DOM 元素操作）。"""
    if env is Environment.BROWSER:
        return (*_COMMON, *ELEMENTS)
    return (*_COMMON, *REQUEST_MODE)
