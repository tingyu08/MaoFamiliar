"""正規化座標（0–999，模型使用）與像素座標的換算。"""

from __future__ import annotations

from familiar.core.models import Frame, Point

NORMALIZED_MAX = 1000


class CoordinateError(ValueError):
    """模型給的座標超出 0–1000 範圍。"""


def _check(value: float, axis: str) -> None:
    if not 0 <= value <= NORMALIZED_MAX:
        raise CoordinateError(f"{axis} 座標 {value} 超出 0–999 範圍")


def _scale(value: float, size: int) -> int:
    return min(size - 1, max(0, round(value / NORMALIZED_MAX * size)))


def to_pixel(x: float, y: float, frame: Frame) -> Point:
    """正規化座標 → 像素。1000 視為邊界，夾在畫面內。"""
    _check(x, "x")
    _check(y, "y")
    return Point(_scale(x, frame.width), _scale(y, frame.height))


def to_normalized(point: Point, frame: Frame) -> tuple[int, int]:
    """像素 → 正規化座標（用於錄製與除錯）。"""
    return (
        round(point.x / frame.width * NORMALIZED_MAX),
        round(point.y / frame.height * NORMALIZED_MAX),
    )
