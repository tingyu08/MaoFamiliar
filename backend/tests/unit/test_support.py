"""事件匯流排、任務控制、影像工具。"""

import asyncio
import base64
import io

import pytest
from PIL import Image, ImageDraw

from familiar.core.control import TaskControl
from familiar.core.events import QUEUE_SIZE, EventBus, make_event
from familiar.vision.images import downscale_png, dhash, hamming, thumbnail_b64, unchanged_streak, zoom_png


def png(color=(20, 20, 20), box=None, size=(320, 200)) -> bytes:
    image = Image.new("RGB", size, color)
    if box:
        ImageDraw.Draw(image).rectangle(box, fill=(250, 250, 250))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


class TestImages:
    def test_same_image_same_hash(self):
        assert hamming(dhash(png(box=(10, 10, 60, 60))), dhash(png(box=(10, 10, 60, 60)))) == 0

    def test_different_images_differ(self):
        assert hamming(dhash(png(box=(10, 10, 60, 60))), dhash(png(box=(200, 100, 300, 190)))) > 4

    def test_unchanged_streak(self):
        a, b = dhash(png(box=(10, 10, 60, 60))), dhash(png(box=(200, 100, 300, 190)))
        assert unchanged_streak(()) == 0
        assert unchanged_streak((a, b, b, b)) == 3
        assert unchanged_streak((b, b, a)) == 1

    def test_thumbnail_is_jpeg(self):
        data = base64.b64decode(thumbnail_b64(png(), width=100))
        assert data[:2] == b"\xff\xd8"
        assert Image.open(io.BytesIO(data)).size == (100, 62)

    def test_downscale(self):
        original = png(size=(2000, 1000))
        smaller = downscale_png(original, 1000)
        assert Image.open(io.BytesIO(smaller)).size == (1000, 500)
        small = png(size=(400, 300))
        assert downscale_png(small, 1000) is small

    def test_zoom_crops_and_enlarges_small_region(self):
        original = png(box=(100, 50, 180, 100))
        zoomed = Image.open(io.BytesIO(zoom_png(original, {"x1": 100, "y1": 50, "x2": 180, "y2": 100}, side=800)))
        assert zoomed.size == (800, 500)  # 80×50 的區域放大到長邊 800，比例不變
        assert zoomed.getpixel((400, 250))[0] > 200  # 裁到的是白色方塊那一塊

    def test_zoom_shrinks_region_larger_than_side(self):
        zoomed = zoom_png(png(size=(2000, 1000)), {"x1": 0, "y1": 0, "x2": 2000, "y2": 1000}, side=1000)
        assert Image.open(io.BytesIO(zoomed)).size == (1000, 500)

    def test_zoom_clamps_to_image_and_rejects_tiny_region(self):
        clamped = zoom_png(png(), {"x1": 300, "y1": 180, "x2": 400, "y2": 260}, side=200)
        assert Image.open(io.BytesIO(clamped)).size == (200, 200)  # 只剩 20×20 在圖內
        with pytest.raises(ValueError):
            zoom_png(png(), {"x1": 10, "y1": 10, "x2": 12, "y2": 40})


class TestEventBus:
    async def test_fan_out_and_unsubscribe(self):
        bus = EventBus()
        q1, q2 = bus.subscribe(), bus.subscribe()
        await bus.emit(make_event("step", index=1))
        assert q1.get_nowait() == {"type": "step", "index": 1}
        assert q2.get_nowait()["index"] == 1
        bus.unsubscribe(q2)
        await bus.emit(make_event("x"))
        assert q2.empty() and not q1.empty()

    async def test_pending_prompts_are_remembered_until_resolved(self):
        bus = EventBus()
        await bus.emit(make_event("confirm_request", request_id="r1"))
        await bus.emit(make_event("step", index=1))
        assert [e["request_id"] for e in bus.pending_prompts()] == ["r1"]
        await bus.emit(make_event("task_status", status="waiting_user"))
        assert len(bus.pending_prompts()) == 1
        await bus.emit(make_event("task_status", status="running"))
        assert bus.pending_prompts() == ()

    async def test_pending_cleared_when_task_finishes(self):
        bus = EventBus()
        await bus.emit(make_event("user_help_request", request_id="h1"))
        await bus.emit(make_event("waiting_quota", ready_at="x"))
        assert [e["type"] for e in bus.pending_prompts()] == ["user_help_request", "waiting_quota"]
        await bus.emit(make_event("task_finished", status="stopped"))
        assert bus.pending_prompts() == ()

    async def test_full_queue_drops_oldest(self):
        bus = EventBus()
        queue = bus.subscribe()
        for i in range(QUEUE_SIZE + 5):
            await bus.emit(make_event("n", i=i))
        assert queue.qsize() == QUEUE_SIZE
        assert queue.get_nowait()["i"] == 5


class TestTaskControl:
    async def test_confirmation_approved(self):
        control = TaskControl()
        waiter = asyncio.create_task(control.wait_confirmation("r1", timeout=5))
        await asyncio.sleep(0)
        assert control.resolve_confirmation("r1", True)
        assert await waiter is True

    async def test_confirmation_timeout_is_denial(self):
        assert await TaskControl().wait_confirmation("r1", timeout=0.01) is False

    async def test_stop_interrupts_waits(self):
        control = TaskControl()
        waiter = asyncio.create_task(control.wait_user_help("h1"))
        await asyncio.sleep(0)
        control.request_stop()
        assert await waiter is False
        assert control.stopped

    async def test_resolve_unknown_request(self):
        assert TaskControl().resolve_user_help("nope") is False

    async def test_sleep_or_stop(self):
        control = TaskControl()
        assert await control.sleep_or_stop(0.01) is False
        control.request_stop()
        assert await control.sleep_or_stop(10) is True

    async def test_wait_after_stop_returns_immediately(self):
        control = TaskControl()
        control.request_stop()
        assert await control.wait_confirmation("r", timeout=10) is False
