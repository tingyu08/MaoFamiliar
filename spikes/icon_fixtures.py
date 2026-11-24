"""純圖示（沒有文字）的合成畫面，用來測 Decisions API 在沒有文字可讀時能不能找對目標。"""

from __future__ import annotations

import json
import random
from dataclasses import asdict
from pathlib import Path
from typing import Callable

from PIL import Image, ImageDraw

TILE = 90
ICON_SCENES = 4
WIDTH, HEIGHT = 1280, 800
FG = (240, 240, 240)

Drawer = Callable[[ImageDraw.ImageDraw, int, int, int], None]


def _envelope(d: ImageDraw.ImageDraw, x: int, y: int, s: int) -> None:
    d.rectangle((x + 15, y + 25, x + s - 15, y + s - 25), outline=FG, width=4)
    d.line((x + 15, y + 25, x + s // 2, y + s // 2, x + s - 15, y + 25), fill=FG, width=4)


def _gear(d: ImageDraw.ImageDraw, x: int, y: int, s: int) -> None:
    cx, cy = x + s // 2, y + s // 2
    for dx, dy in ((0, -1), (0, 1), (-1, 0), (1, 0), (-0.7, -0.7), (0.7, 0.7), (-0.7, 0.7), (0.7, -0.7)):
        px, py = cx + dx * 30, cy + dy * 30
        d.rectangle((px - 7, py - 7, px + 7, py + 7), fill=FG)
    d.ellipse((cx - 24, cy - 24, cx + 24, cy + 24), fill=FG)
    d.ellipse((cx - 10, cy - 10, cx + 10, cy + 10), fill=(60, 60, 80))


def _gift(d: ImageDraw.ImageDraw, x: int, y: int, s: int) -> None:
    d.rectangle((x + 18, y + 35, x + s - 18, y + s - 15), outline=FG, width=4)
    d.line((x + s // 2, y + 35, x + s // 2, y + s - 15), fill=FG, width=4)
    d.line((x + 18, y + 50, x + s - 18, y + 50), fill=FG, width=4)
    d.ellipse((x + s // 2 - 20, y + 18, x + s // 2, y + 36), outline=FG, width=4)
    d.ellipse((x + s // 2, y + 18, x + s // 2 + 20, y + 36), outline=FG, width=4)


def _cart(d: ImageDraw.ImageDraw, x: int, y: int, s: int) -> None:
    d.line((x + 12, y + 22, x + 24, y + 22, x + 32, y + 58, x + s - 18, y + 58, x + s - 12, y + 32, x + 28, y + 32),
           fill=FG, width=4)
    d.ellipse((x + 32, y + 64, x + 42, y + 74), fill=FG)
    d.ellipse((x + s - 32, y + 64, x + s - 22, y + 74), fill=FG)


def _house(d: ImageDraw.ImageDraw, x: int, y: int, s: int) -> None:
    d.polygon((x + s // 2, y + 15, x + 15, y + 45, x + s - 15, y + 45), outline=FG, width=4)
    d.rectangle((x + 25, y + 45, x + s - 25, y + s - 15), outline=FG, width=4)


def _magnifier(d: ImageDraw.ImageDraw, x: int, y: int, s: int) -> None:
    d.ellipse((x + 18, y + 18, x + 58, y + 58), outline=FG, width=5)
    d.line((x + 54, y + 54, x + s - 15, y + s - 15), fill=FG, width=7)


def _bell(d: ImageDraw.ImageDraw, x: int, y: int, s: int) -> None:
    d.chord((x + 22, y + 15, x + s - 22, y + 75), start=180, end=360, fill=FG)
    d.rectangle((x + 22, y + 45, x + s - 22, y + 62), fill=FG)
    d.ellipse((x + s // 2 - 6, y + 64, x + s // 2 + 6, y + 76), fill=FG)


def _trash(d: ImageDraw.ImageDraw, x: int, y: int, s: int) -> None:
    d.line((x + 18, y + 25, x + s - 18, y + 25), fill=FG, width=5)
    d.rectangle((x + s // 2 - 8, y + 15, x + s // 2 + 8, y + 25), outline=FG, width=3)
    d.polygon((x + 24, y + 30, x + s - 24, y + 30, x + s - 30, y + s - 12, x + 30, y + s - 12), outline=FG, width=4)


ICONS: dict[str, tuple[Drawer, str]] = {
    "envelope": (_envelope, "查看系統寄給我的信件與補償"),
    "gear": (_gear, "調整遊戲的音量與畫質"),
    "gift": (_gift, "領取免費的每日禮物"),
    "cart": (_cart, "購買新的道具"),
    "house": (_house, "回到遊戲主畫面"),
    "magnifier": (_magnifier, "搜尋其他玩家"),
    "bell": (_bell, "查看最新的通知訊息"),
    "trash": (_trash, "丟掉不需要的道具"),
}


def _place(rng: random.Random, count: int) -> list[tuple[int, int]]:
    spots: list[tuple[int, int]] = []
    while len(spots) < count:
        x, y = rng.randint(40, WIDTH - TILE - 40), rng.randint(40, HEIGHT - TILE - 40)
        if all(abs(x - sx) > TILE + 30 or abs(y - sy) > TILE + 30 for sx, sy in spots):
            spots = [*spots, (x, y)]
    return spots


def make_icon_scene(rng: random.Random, index: int, out_dir: Path) -> dict:
    img = Image.new("RGB", (WIDTH, HEIGHT), (35, 40, 55))
    d = ImageDraw.Draw(img)
    names = list(ICONS)
    rng.shuffle(names)
    elements = []
    for name, (x, y) in zip(names, _place(rng, len(names))):
        d.rounded_rectangle((x, y, x + TILE, y + TILE), radius=16, fill=(60, 60, 80), outline=(120, 120, 150), width=2)
        ICONS[name][0](d, x, y, TILE)
        elements = [*elements, {"label": name, "box": {"x1": x, "y1": y, "x2": x + TILE, "y2": y + TILE}}]
    target = elements[rng.randrange(len(elements))]
    path = out_dir / f"icons_{index:02d}.png"
    img.save(path)
    return {"file": path.name, "target_label": target["label"], "target_box": target["box"],
            "target_goal": ICONS[target["label"]][1], "elements": elements,
            "width": WIDTH, "height": HEIGHT}


def generate(out_dir: Path, seed: int) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    rng = random.Random(seed)
    rows = [make_icon_scene(rng, i, out_dir) for i in range(ICON_SCENES)]
    (out_dir / "labels.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
