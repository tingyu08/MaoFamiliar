"""custom_fc 驗證用的最小動作函式宣告與座標工具。"""

from __future__ import annotations

from typing import Any

NORMALIZED_MAX = 1000

SYSTEM_INSTRUCTION = (
    "你正在操作一個應用程式畫面。每次都會收到目前的截圖。"
    "座標使用 0 到 999 的正規化整數：x 從左到右，y 從上到下。"
    "需要點擊時呼叫 click，完成時呼叫 task_done。不要只用文字回答。"
)


def _fn(name: str, description: str, properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "type": "function",
        "name": name,
        "description": description,
        "parameters": {"type": "object", "properties": properties, "required": required},
    }


CLICK_TOOL = _fn(
    "click",
    "點擊畫面上的一個位置。x、y 是 0–999 的正規化座標。",
    {
        "x": {"type": "integer", "description": "0–999，畫面左緣為 0"},
        "y": {"type": "integer", "description": "0–999，畫面上緣為 0"},
        "intent": {"type": "string", "description": "這一步要做什麼"},
    },
    ["x", "y", "intent"],
)

TASK_DONE_TOOL = _fn(
    "task_done",
    "任務完成時呼叫。",
    {"summary": {"type": "string"}},
    ["summary"],
)

CUSTOM_TOOLS: tuple[dict[str, Any], ...] = (CLICK_TOOL, TASK_DONE_TOOL)


def to_pixels(x: float, y: float, width: int, height: int) -> tuple[int, int]:
    """正規化座標（0–999）換算成像素。"""
    return int(x / NORMALIZED_MAX * width), int(y / NORMALIZED_MAX * height)


def inside(px: int, py: int, box: dict[str, int]) -> bool:
    return box["x1"] <= px <= box["x2"] and box["y1"] <= py <= box["y2"]


def distance_to_center(px: int, py: int, box: dict[str, int]) -> float:
    cx, cy = (box["x1"] + box["x2"]) / 2, (box["y1"] + box["y2"]) / 2
    return ((px - cx) ** 2 + (py - cy) ** 2) ** 0.5
