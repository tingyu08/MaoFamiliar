"""截圖處理：差異雜湊（卡住偵測）、前端縮圖、送模型前縮小。"""

from __future__ import annotations

import base64
import io

from PIL import Image

HASH_SIZE = 8
SIMILAR_BITS = 4           # 64 位元中差異 ≤ 4 視為「幾乎相同」
THUMB_WIDTH = 480
THUMB_QUALITY = 60


def _open(png: bytes) -> Image.Image:
    return Image.open(io.BytesIO(png))


def dhash(png: bytes) -> str:
    """差異雜湊：縮成 9×8 灰階，比較相鄰像素。"""
    gray = _open(png).convert("L").resize((HASH_SIZE + 1, HASH_SIZE), Image.Resampling.LANCZOS)
    pixels = list(gray.tobytes())  # 灰階每像素 1 位元組
    bits = 0
    for row in range(HASH_SIZE):
        for col in range(HASH_SIZE):
            left = pixels[row * (HASH_SIZE + 1) + col]
            right = pixels[row * (HASH_SIZE + 1) + col + 1]
            bits = (bits << 1) | (1 if left > right else 0)
    return f"{bits:016x}"


def hamming(a: str, b: str) -> int:
    return bin(int(a, 16) ^ int(b, 16)).count("1")


def unchanged_streak(hashes: tuple[str, ...]) -> int:
    """從最新一張往回數，有幾張與它幾乎相同（含自己）。"""
    if not hashes:
        return 0
    latest = hashes[-1]
    streak = 0
    for value in reversed(hashes):
        if hamming(value, latest) > SIMILAR_BITS:
            break
        streak += 1
    return streak


def thumbnail_b64(png: bytes, width: int = THUMB_WIDTH) -> str:
    image = _open(png).convert("RGB")
    height = max(1, round(image.height * width / image.width))
    buffer = io.BytesIO()
    image.resize((width, height), Image.Resampling.LANCZOS).save(buffer, format="JPEG", quality=THUMB_QUALITY)
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def downscale_png(png: bytes, max_side: int) -> bytes:
    """長邊超過 max_side 才縮小（NFR-004）。座標仍以原始大小換算，因為模型用正規化座標。"""
    image = _open(png)
    longest = max(image.size)
    if longest <= max_side:
        return png
    scale = max_side / longest
    resized = image.resize((round(image.width * scale), round(image.height * scale)), Image.Resampling.LANCZOS)
    buffer = io.BytesIO()
    resized.save(buffer, format="PNG")
    return buffer.getvalue()
