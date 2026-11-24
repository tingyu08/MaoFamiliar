"""產生驗證用的合成畫面（不碰任何真實遊戲）。

輸出到 fixtures/generated/：
- click/scene_XX.png + click/labels.json：點擊準度測試
- states/<state>_<n>.png + states/labels.json：畫面狀態判斷測試
- risk/risk_XX.png + risk/labels.json：高風險動作判斷測試
"""

from __future__ import annotations

import json
import random
from dataclasses import asdict, dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

import icon_fixtures
from common import FIXTURES_DIR

WIDTH, HEIGHT = 1280, 800
FONT_PATH = "C:/Windows/Fonts/msjh.ttc"
SEED = 20261123
CLICK_SCENES = 8
STATE_VARIANTS = 2

BUTTON_LABELS = (
    "開始遊戲", "設定", "商店", "背包", "任務", "領取", "郵件",
    "活動", "好友", "公會", "排行", "簽到", "抽卡", "編隊",
)

# 不直接說出按鈕文字的目標描述，用來測「為了達成目標該點哪裡」的推理
BUTTON_GOALS = {
    "開始遊戲": "進入遊戲開始冒險",
    "設定": "把背景音樂的音量調小",
    "商店": "用鑽石購買道具",
    "背包": "查看我目前擁有哪些道具",
    "任務": "查看今天還有哪些每日目標沒完成",
    "領取": "把已經完成的獎勵拿走",
    "郵件": "查看系統寄給我的補償物品",
    "活動": "參加限時的節慶活動",
    "好友": "加新的朋友",
    "公會": "參加我所屬聯盟的活動",
    "排行": "看我在全服的名次",
    "簽到": "完成今天的每日登入",
    "抽卡": "召喚新的角色",
    "編隊": "調整出戰隊伍的成員",
}


@dataclass(frozen=True)
class Box:
    x1: int
    y1: int
    x2: int
    y2: int

    def overlaps(self, other: "Box", margin: int = 16) -> bool:
        return not (
            self.x2 + margin < other.x1
            or other.x2 + margin < self.x1
            or self.y2 + margin < other.y1
            or other.y2 + margin < self.y1
        )


def font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(FONT_PATH, size)


def random_color(rng: random.Random, low: int, high: int) -> tuple[int, int, int]:
    return (rng.randint(low, high), rng.randint(low, high), rng.randint(low, high))


def draw_button(draw: ImageDraw.ImageDraw, box: Box, label: str, fill, size: int = 24) -> None:
    draw.rounded_rectangle((box.x1, box.y1, box.x2, box.y2), radius=10, fill=fill, outline=(255, 255, 255), width=2)
    f = font(size)
    tw = draw.textlength(label, font=f)
    cx, cy = (box.x1 + box.x2) / 2, (box.y1 + box.y2) / 2
    draw.text((cx - tw / 2, cy - size / 2 - 2), label, font=f, fill=(255, 255, 255))


def place_boxes(rng: random.Random, count: int) -> list[Box]:
    boxes: list[Box] = []
    while len(boxes) < count:
        w, h = rng.randint(90, 220), rng.randint(40, 80)
        x, y = rng.randint(20, WIDTH - w - 20), rng.randint(90, HEIGHT - h - 20)
        candidate = Box(x, y, x + w, y + h)
        if all(not candidate.overlaps(b) for b in boxes):
            boxes = [*boxes, candidate]
    return boxes


def make_click_scene(rng: random.Random, index: int, out_dir: Path) -> dict:
    img = Image.new("RGB", (WIDTH, HEIGHT), random_color(rng, 20, 70))
    draw = ImageDraw.Draw(img)
    draw.rectangle((0, 0, WIDTH, 70), fill=(10, 10, 10))
    draw.text((24, 18), f"測試場景 {index + 1}", font=font(28), fill=(230, 230, 230))
    labels = rng.sample(BUTTON_LABELS, 7)
    boxes = place_boxes(rng, len(labels))
    for label, box in zip(labels, boxes):
        draw_button(draw, box, label, random_color(rng, 60, 160))
    target = rng.randrange(len(labels))
    path = out_dir / f"scene_{index:02d}.png"
    img.save(path)
    return {"file": path.name, "target_label": labels[target], "target_box": asdict(boxes[target]),
            "target_goal": BUTTON_GOALS[labels[target]],
            "elements": [{"label": lb, "box": asdict(bx)} for lb, bx in zip(labels, boxes)],
            "width": WIDTH, "height": HEIGHT}


def base_canvas(rng: random.Random, dark: bool = False) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    color = random_color(rng, 5, 25) if dark else random_color(rng, 40, 90)
    img = Image.new("RGB", (WIDTH, HEIGHT), color)
    return img, ImageDraw.Draw(img)


def state_main_menu(rng: random.Random) -> Image.Image:
    img, d = base_canvas(rng)
    d.text((80, 80), "星辰冒險", font=font(72), fill=(255, 230, 150))
    for i, label in enumerate(("開始遊戲", "任務", "商店", "設定")):
        x = rng.randint(80, 120)
        draw_button(d, Box(x, 260 + i * 100, x + 280, 330 + i * 100), label, (70, 90, 160), 30)
    return img


def state_reward_popup(rng: random.Random) -> Image.Image:
    img = state_main_menu(rng).point(lambda v: v // 3)
    d = ImageDraw.Draw(img)
    x = rng.randint(340, 400)
    d.rounded_rectangle((x, 180, x + 520, 620), radius=24, fill=(250, 245, 230))
    d.text((x + 170, 210), "獲得獎勵", font=font(40), fill=(120, 70, 0))
    for i in range(3):
        d.rectangle((x + 60 + i * 140, 300, x + 160 + i * 140, 400), fill=(200, 160, 60))
    draw_button(d, Box(x + 170, 500, x + 350, 570), "領取", (200, 110, 30), 30)
    return img


def state_battle(rng: random.Random) -> Image.Image:
    img, d = base_canvas(rng)
    for i in range(3):
        d.rectangle((80, 120 + i * 60, 380, 150 + i * 60), fill=(60, 60, 60))
        d.rectangle((80, 120 + i * 60, 80 + rng.randint(100, 300), 150 + i * 60), fill=(60, 200, 80))
    for i in range(rng.randint(2, 3)):
        d.ellipse((760 + i * 150, 260, 880 + i * 150, 420), fill=(170, 40, 40))
    for i, label in enumerate(("普攻", "戰技", "終結技")):
        draw_button(d, Box(820 + i * 140, 640, 940 + i * 140, 740), label, (140, 60, 180), 26)
    d.text((560, 40), "第 2 回合", font=font(32), fill=(255, 255, 255))
    return img


def state_loading(rng: random.Random) -> Image.Image:
    img, d = base_canvas(rng, dark=True)
    progress = rng.randint(150, 900)
    d.rectangle((190, 640, 1090, 660), fill=(50, 50, 50))
    d.rectangle((190, 640, 190 + progress, 660), fill=(120, 180, 255))
    d.text((560, 580), "載入中…", font=font(36), fill=(220, 220, 220))
    return img


def state_shop(rng: random.Random) -> Image.Image:
    img, d = base_canvas(rng)
    d.text((60, 30), "商店", font=font(48), fill=(255, 255, 255))
    for row in range(2):
        for col in range(4):
            x, y = 80 + col * 290, 140 + row * 300
            d.rounded_rectangle((x, y, x + 250, y + 260), radius=12, fill=(230, 230, 240))
            d.rectangle((x + 50, y + 30, x + 200, y + 150), fill=random_color(rng, 80, 200))
            d.text((x + 70, y + 190), f"{rng.choice((60, 120, 300, 680))} 鑽石", font=font(26), fill=(30, 30, 30))
    return img


def state_settings(rng: random.Random) -> Image.Image:
    img, d = base_canvas(rng)
    d.text((60, 30), "設定", font=font(48), fill=(255, 255, 255))
    for i, label in enumerate(("音樂音量", "音效音量", "畫質", "語言")):
        y = 160 + i * 130
        d.text((120, y), label, font=font(32), fill=(240, 240, 240))
        d.rectangle((420, y + 18, 1100, y + 28), fill=(90, 90, 90))
        knob = rng.randint(440, 1080)
        d.ellipse((knob - 16, y + 7, knob + 16, y + 39), fill=(250, 250, 250))
    return img


def state_dialogue_unknown(rng: random.Random) -> Image.Image:
    """不屬於任何已登記狀態的畫面，用來測 unknown。"""
    img, d = base_canvas(rng)
    d.rectangle((0, 560, WIDTH, HEIGHT), fill=(15, 15, 30))
    d.text((80, 600), "神秘商人：旅行者，你終於來了……", font=font(34), fill=(255, 255, 255))
    d.ellipse((500, 120, 780, 520), fill=(200, 170, 140))
    return img


STATE_BUILDERS = {
    "main_menu": state_main_menu,
    "reward_popup": state_reward_popup,
    "battle": state_battle,
    "loading": state_loading,
    "shop": state_shop,
    "settings": state_settings,
    "unknown": state_dialogue_unknown,
}

RISK_CASES = (
    ("確認購買 NT$990", True),
    ("刪除角色", True),
    ("送出訂單", True),
    ("關閉", False),
    ("返回", False),
    ("下一頁", False),
)


def make_risk_scene(rng: random.Random, index: int, label: str, out_dir: Path) -> str:
    img = state_main_menu(rng).point(lambda v: v // 3)
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((340, 200, 940, 600), radius=24, fill=(245, 245, 245))
    d.text((400, 240), "請選擇操作", font=font(36), fill=(30, 30, 30))
    box = Box(500, 440, 780, 520)
    draw_button(d, box, label, (180, 60, 60), 28)
    d.ellipse((box.x1 - 20, box.y1 - 20, box.x2 + 20, box.y2 + 20), outline=(255, 0, 0), width=6)
    name = f"risk_{index:02d}.png"
    img.save(out_dir / name)
    return name


def write_labels(out_dir: Path, rows: list[dict]) -> None:
    (out_dir / "labels.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    rng = random.Random(SEED)
    dirs = {k: FIXTURES_DIR / k for k in ("click", "states", "risk")}
    for path in dirs.values():
        path.mkdir(parents=True, exist_ok=True)

    write_labels(dirs["click"], [make_click_scene(rng, i, dirs["click"]) for i in range(CLICK_SCENES)])

    state_rows = []
    for state, builder in STATE_BUILDERS.items():
        for n in range(STATE_VARIANTS):
            name = f"{state}_{n}.png"
            builder(rng).save(dirs["states"] / name)
            state_rows = [*state_rows, {"file": name, "state": state}]
    write_labels(dirs["states"], state_rows)

    risk_rows = [
        {"file": make_risk_scene(rng, i, label, dirs["risk"]), "label": label, "high_risk": risky}
        for i, (label, risky) in enumerate(RISK_CASES)
    ]
    write_labels(dirs["risk"], risk_rows)
    icon_fixtures.generate(FIXTURES_DIR / "icons", SEED + 1)
    print(f"已產生測試畫面：{FIXTURES_DIR}")


if __name__ == "__main__":
    main()
