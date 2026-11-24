"""在截圖上疊加編號標記（Set-of-Mark）或格子，讓 Decisions API 用「選項」表達位置。"""

from __future__ import annotations

import string
from dataclasses import dataclass

from PIL import Image, ImageDraw, ImageFont

FONT_PATH = "C:/Windows/Fonts/msjh.ttc"
TAG_FILL = (255, 220, 0)
TAG_TEXT = (0, 0, 0)
GRID_LINE = (255, 220, 0)


@dataclass(frozen=True)
class Cell:
    cell_id: str
    x1: float
    y1: float
    x2: float
    y2: float

    @property
    def center(self) -> tuple[float, float]:
        return (self.x1 + self.x2) / 2, (self.y1 + self.y2) / 2


def _tag(draw: ImageDraw.ImageDraw, x: float, y: float, text: str, size: int) -> None:
    font = ImageFont.truetype(FONT_PATH, size)
    width = draw.textlength(text, font=font)
    draw.rectangle((x, y, x + width + 8, y + size + 6), fill=TAG_FILL)
    draw.text((x + 4, y + 1), text, font=font, fill=TAG_TEXT)


def draw_marks(image: Image.Image, boxes: list[dict[str, int]]) -> Image.Image:
    """在每個候選框左上角畫上編號 1..n，回傳新圖（不修改原圖）。"""
    marked = image.copy()
    draw = ImageDraw.Draw(marked)
    for index, box in enumerate(boxes, start=1):
        draw.rectangle((box["x1"], box["y1"], box["x2"], box["y2"]), outline=TAG_FILL, width=2)
        _tag(draw, box["x1"] - 4, box["y1"] - 26, str(index), 20)
    return marked


def grid_cells(width: float, height: float, cols: int, rows: int, origin=(0.0, 0.0),
               lowercase: bool = False) -> list[Cell]:
    letters = string.ascii_lowercase if lowercase else string.ascii_uppercase
    cw, ch = width / cols, height / rows
    ox, oy = origin
    return [
        Cell(f"{letters[r]}{c + 1}", ox + c * cw, oy + r * ch, ox + (c + 1) * cw, oy + (r + 1) * ch)
        for r in range(rows)
        for c in range(cols)
    ]


def draw_grid(image: Image.Image, cols: int, rows: int, lowercase: bool = False) -> tuple[Image.Image, list[Cell]]:
    """畫格線與格子代號，回傳新圖與以「這張圖」為座標系的格子清單。"""
    gridded = image.copy()
    draw = ImageDraw.Draw(gridded)
    cells = grid_cells(image.width, image.height, cols, rows, lowercase=lowercase)
    for cell in cells:
        draw.rectangle((cell.x1, cell.y1, cell.x2, cell.y2), outline=GRID_LINE, width=1)
        _tag(draw, cell.x1 + 2, cell.y1 + 2, cell.cell_id, 14)
    return gridded, cells


def neighborhood(cell: Cell, cells: list[Cell], width: float, height: float) -> tuple[int, int, int, int]:
    """以選中的格子為中心，往外擴一格的裁切範圍（像素，已夾在畫面內）。"""
    cw, ch = cell.x2 - cell.x1, cell.y2 - cell.y1
    return (
        int(max(0, cell.x1 - cw)),
        int(max(0, cell.y1 - ch)),
        int(min(width, cell.x2 + cw)),
        int(min(height, cell.y2 + ch)),
    )
