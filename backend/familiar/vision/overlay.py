"""在截圖副本上畫黃色編號標記（Set-of-Mark），讓 Decisions API 用選項表達位置（第 0 週 spike 04）。"""

from __future__ import annotations

import io
from functools import lru_cache
from pathlib import Path
from typing import Sequence

from PIL import Image, ImageDraw, ImageFont

TAG_FILL = (255, 220, 0)
TAG_TEXT = (0, 0, 0)
FONT_CANDIDATES = ("C:/Windows/Fonts/msjh.ttc", "C:/Windows/Fonts/arial.ttf",
                   "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")


@lru_cache(maxsize=4)
def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for candidate in FONT_CANDIDATES:
        if Path(candidate).exists():
            return ImageFont.truetype(candidate, size)
    return ImageFont.load_default()


def draw_marks(png: bytes, boxes: Sequence[dict[str, float]], size: int = 20) -> bytes:
    """在每個框的左上角畫上 1..n 編號，回傳新的 PNG（不修改原圖）。"""
    image = Image.open(io.BytesIO(png)).convert("RGB")
    draw = ImageDraw.Draw(image)
    font = _font(size)
    for index, box in enumerate(boxes, start=1):
        draw.rectangle((box["x1"], box["y1"], box["x2"], box["y2"]), outline=TAG_FILL, width=2)
        label = str(index)
        width = draw.textlength(label, font=font)
        x, y = max(0, box["x1"] - 4), max(0, box["y1"] - size - 6)
        draw.rectangle((x, y, x + width + 8, y + size + 6), fill=TAG_FILL)
        draw.text((x + 4, y + 1), label, font=font, fill=TAG_TEXT)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()
