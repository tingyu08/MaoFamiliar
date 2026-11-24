"""按鍵名稱對照：模型常用的小寫名稱 → Playwright 按鍵名稱。"""

from __future__ import annotations

from typing import Sequence

_ALIASES = {
    "ctrl": "Control", "control": "Control", "alt": "Alt", "shift": "Shift",
    "meta": "Meta", "cmd": "Meta", "win": "Meta", "super": "Meta",
    "enter": "Enter", "return": "Enter", "esc": "Escape", "escape": "Escape",
    "tab": "Tab", "space": "Space", "backspace": "Backspace", "delete": "Delete", "del": "Delete",
    "up": "ArrowUp", "down": "ArrowDown", "left": "ArrowLeft", "right": "ArrowRight",
    "arrowup": "ArrowUp", "arrowdown": "ArrowDown", "arrowleft": "ArrowLeft", "arrowright": "ArrowRight",
    "home": "Home", "end": "End", "pageup": "PageUp", "pagedown": "PageDown", "insert": "Insert",
}


def to_playwright_key(key: str) -> str:
    lowered = key.strip().lower()
    if lowered in _ALIASES:
        return _ALIASES[lowered]
    if len(lowered) in (2, 3) and lowered[0] == "f" and lowered[1:].isdigit():
        return lowered.upper()  # f1–f12
    return lowered if len(lowered) == 1 else key.strip()


def to_playwright_combo(keys: Sequence[str]) -> str:
    return "+".join(to_playwright_key(k) for k in keys)
