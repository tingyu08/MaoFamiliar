"""可互動元素清單（給模型的 click_element／fill_element 用，也是第 3 層選候選的來源）。"""

from __future__ import annotations

from dataclasses import dataclass

MAX_NAME_CHARS = 60


@dataclass(frozen=True)
class Element:
    ref: str
    role: str
    name: str
    x1: float
    y1: float
    x2: float
    y2: float
    is_password: bool = False

    @property
    def center(self) -> tuple[int, int]:
        return round((self.x1 + self.x2) / 2), round((self.y1 + self.y2) / 2)


@dataclass(frozen=True)
class ElementTable:
    elements: tuple[Element, ...] = ()

    def find(self, ref: str) -> Element | None:
        return next((e for e in self.elements if e.ref == ref), None)

    def to_text(self) -> str:
        """精簡文字格式：每行「e3 button「登入」」。"""
        lines = []
        for e in self.elements:
            label = "（密碼欄位）" if e.is_password else f"「{e.name[:MAX_NAME_CHARS]}」"
            lines.append(f"{e.ref} {e.role}{label}")
        return "\n".join(lines)
