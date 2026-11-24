"""BrowserExecutor：用 Playwright（Chromium）執行動作。"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, Awaitable, Callable

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import async_playwright

from familiar.core.models import Action, ActionKind, Environment, Frame
from familiar.executors.base import ELEMENT_KINDS, ExecResult, Screenshot
from familiar.executors.dom_snapshot import describe_point, describe_ref, mark_css, ref_selector, snapshot_elements
from familiar.executors.elements import ElementTable
from familiar.executors.keys import to_playwright_combo, to_playwright_key

logger = logging.getLogger(__name__)

NAV_TIMEOUT_MS = 30_000
ELEMENT_TIMEOUT_MS = 5_000
SETTLE_TIMEOUT_MS = 3_000
SCROLL_PIXELS_PER_NOTCH = 100
TAP_GAP_SEC = 0.05
TAP_HOLD_MS = 100  # 24fps 的 Flash 遊戲一格約 42ms；實測瞬間按放幾乎都被漏掉，按住才有反應
# 網頁遊戲畫面：可見範圍內最大的 canvas／iframe／embed／object／video（至少 200×150）
_FOCUS_JS = r"""() => {
  const vw = window.innerWidth, vh = window.innerHeight;
  let best = null, bestArea = 0;
  for (const el of document.querySelectorAll('canvas, iframe, embed, object, video, ruffle-player, ruffle-embed')) {
    const r = el.getBoundingClientRect();
    const x1 = Math.max(0, r.left), y1 = Math.max(0, r.top), x2 = Math.min(vw, r.right), y2 = Math.min(vh, r.bottom);
    const area = (x2 - x1) * (y2 - y1);
    if (x2 - x1 >= 200 && y2 - y1 >= 150 && area > bestArea) { best = {x1, y1, x2, y2}; bestArea = area; }
  }
  return best && {x1: Math.round(best.x1), y1: Math.round(best.y1), x2: Math.round(best.x2), y2: Math.round(best.y2)};
}"""
# 遊戲畫面有一部分在視窗外（例如下緣被切掉）時，捲動頁面讓它整個露出來（放得下才捲）
_REVEAL_FOCUS_JS = r"""() => {
  const vw = window.innerWidth, vh = window.innerHeight;
  let best = null, bestArea = 0;
  for (const el of document.querySelectorAll('canvas, iframe, embed, object, video, ruffle-player, ruffle-embed')) {
    const r = el.getBoundingClientRect();
    const w = Math.min(vw, r.right) - Math.max(0, r.left), h = Math.min(vh, r.bottom) - Math.max(0, r.top);
    if (w >= 200 && h >= 150 && w * h > bestArea) { best = r; bestArea = w * h; }
  }
  if (!best || best.height > vh || (best.top >= 0 && best.bottom <= vh)) return false;
  window.scrollBy(0, best.top - (vh - best.height) / 2);
  return true;
}"""
_IS_PASSWORD_FOCUSED_JS = (
    "() => { const e = document.activeElement;"
    " return !!e && e.tagName === 'INPUT' && (e.type || '').toLowerCase() === 'password'; }"
)

Handler = Callable[[Any, Action], Awaitable[None]]


class BrowserExecutor:
    environment = Environment.BROWSER

    def __init__(self, profile_dir: Path | None, headless: bool = False, viewport: tuple[int, int] = (1280, 800)):
        self._profile_dir = profile_dir
        self._headless = headless
        self._viewport = {"width": viewport[0], "height": viewport[1]}
        self._playwright: Any = None
        self._browser: Any = None
        self._context: Any = None
        self._page: Any = None
        self._table = ElementTable()

    @property
    def page(self) -> Any:
        if self._page is None:
            raise RuntimeError("瀏覽器尚未啟動")
        return self._page

    async def start(self, target: str) -> None:
        self._playwright = await async_playwright().start()
        chromium = self._playwright.chromium
        if self._profile_dir:
            self._profile_dir.mkdir(parents=True, exist_ok=True)
            self._context = await chromium.launch_persistent_context(
                str(self._profile_dir), headless=self._headless, viewport=self._viewport)
        else:
            self._browser = await chromium.launch(headless=self._headless)
            self._context = await self._browser.new_context(viewport=self._viewport)
        self._context.on("page", self._adopt_page)
        self._page = self._context.pages[0] if self._context.pages else await self._context.new_page()
        if target:
            await self._page.goto(target, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)

    def _adopt_page(self, page: Any) -> None:
        """網頁開了新分頁時，後續操作改到新分頁。"""
        self._page = page

    async def close(self) -> None:
        for closer in (self._context, self._browser):
            if closer is not None:
                try:
                    await closer.close()
                except PlaywrightError:
                    logger.debug("關閉瀏覽器時發生錯誤", exc_info=True)
        if self._playwright is not None:
            await self._playwright.stop()
        self._playwright = self._browser = self._context = self._page = None

    async def screenshot(self) -> Screenshot:
        png = await self.page.screenshot(type="png", scale="css")
        size = self.page.viewport_size or self._viewport
        return Screenshot(png=png, frame=Frame(size["width"], size["height"]))

    async def context_info(self) -> dict[str, str]:
        return {"url": self.page.url, "title": await self.page.title()}

    async def elements(self) -> ElementTable:
        self._table = await snapshot_elements(self.page)
        return self._table

    async def is_password_target(self, action: Action) -> bool:
        if action.kind is ActionKind.FILL_ELEMENT:
            element = self._table.find(str(action.args.get("ref")))
            return bool(element and element.is_password)
        if action.kind is ActionKind.TYPE_TEXT:
            return bool(await self.page.evaluate(_IS_PASSWORD_FOCUSED_JS))
        return False

    async def execute(self, action: Action) -> ExecResult:
        handler = _HANDLERS.get(action.kind)
        if handler is None:
            return ExecResult(False, f"瀏覽器不支援這個動作：{action.kind.value}")
        locator = await self._locate(action)  # 動作前先記，點擊後元素可能就消失了
        try:
            await handler(self.page, action)
            await self._settle()
        except (PlaywrightError, asyncio.TimeoutError) as error:
            logger.info("動作執行失敗 %s：%s", action.kind.value, error)
            return ExecResult(False, _short(error, action))
        return ExecResult(True, locator=locator)

    async def focus_box(self) -> dict[str, int] | None:
        """網頁裡的遊戲畫面區域；即時模式的卡住偵測只看這一塊（四周的網站框架和廣告不會動）。"""
        try:
            return await self.page.evaluate(_FOCUS_JS)
        except PlaywrightError:
            return None

    async def reveal_focus(self) -> bool:
        """遊戲畫面被視窗切掉一部分時捲動頁面讓它完整露出；回傳是否有捲動。"""
        try:
            return bool(await self.page.evaluate(_REVEAL_FOCUS_JS))
        except PlaywrightError:
            return False

    async def element_at(self, x: float, y: float) -> dict[str, Any] | None:
        """座標上（最接近的可互動）元素的描述；重播用座標定位前先確認。"""
        try:
            return await describe_point(self.page, x, y)
        except PlaywrightError:
            return None

    async def ref_for_css(self, css: str) -> str | None:
        try:
            return await mark_css(self.page, css)
        except PlaywrightError:
            return None

    async def _locate(self, action: Action) -> dict[str, Any] | None:
        """技能錄製用的定位資訊：role、名稱、CSS 路徑、位置與畫面大小。"""
        if action.kind.value not in ELEMENT_KINDS:
            return None
        try:
            if "ref" in action.args:
                described = await describe_ref(self.page, str(action.args["ref"]))
            else:
                described = await describe_point(self.page, action.args["x"], action.args["y"])
        except PlaywrightError:
            return None
        if not described:
            return None
        size = self.page.viewport_size or self._viewport
        point = ((action.args["x"], action.args["y"]) if "x" in action.args else
                 (round((described["x1"] + described["x2"]) / 2), round((described["y1"] + described["y2"]) / 2)))
        return {**described, "point": list(point), "frame": [size["width"], size["height"]]}

    async def _settle(self) -> None:
        try:
            await self.page.wait_for_load_state("domcontentloaded", timeout=SETTLE_TIMEOUT_MS)
        except PlaywrightError:
            pass  # 頁面持續載入不算失敗


def _short(error: Exception, action: Action) -> str:
    if action.kind in (ActionKind.CLICK_ELEMENT, ActionKind.FILL_ELEMENT):
        return f"找不到或無法操作元素 {action.args.get('ref')}，請重新查看元素清單"
    return str(error).splitlines()[0][:200]


async def _click(page: Any, a: Action) -> None:
    await page.mouse.click(a.args["x"], a.args["y"])


async def _double_click(page: Any, a: Action) -> None:
    await page.mouse.dblclick(a.args["x"], a.args["y"])


async def _right_click(page: Any, a: Action) -> None:
    await page.mouse.click(a.args["x"], a.args["y"], button="right")


async def _drag(page: Any, a: Action) -> None:
    await page.mouse.move(a.args["x1"], a.args["y1"])
    await page.mouse.down()
    await page.mouse.move(a.args["x2"], a.args["y2"], steps=10)
    await page.mouse.up()


async def _scroll(page: Any, a: Action) -> None:
    distance = a.args["amount"] * SCROLL_PIXELS_PER_NOTCH
    dx, dy = {"up": (0, -distance), "down": (0, distance),
              "left": (-distance, 0), "right": (distance, 0)}[a.args["direction"]]
    await page.mouse.move(a.args["x"], a.args["y"])
    await page.mouse.wheel(dx, dy)


async def _type_text(page: Any, a: Action) -> None:
    await page.keyboard.type(a.args["text"], delay=15)
    if a.args.get("press_enter"):
        await page.keyboard.press("Enter")


async def _press_keys(page: Any, a: Action) -> None:
    await page.keyboard.press(to_playwright_combo(a.args["keys"]))


async def _navigate(page: Any, a: Action) -> None:
    await page.goto(a.args["url"], wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)


async def _go_back(page: Any, a: Action) -> None:
    await page.go_back(wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)


async def _click_element(page: Any, a: Action) -> None:
    await page.locator(ref_selector(a.args["ref"])).click(timeout=ELEMENT_TIMEOUT_MS)


async def _fill_element(page: Any, a: Action) -> None:
    await page.locator(ref_selector(a.args["ref"])).fill(a.args["text"], timeout=ELEMENT_TIMEOUT_MS)


async def _wait(page: Any, a: Action) -> None:
    await asyncio.sleep(float(a.args.get("seconds", 1)))


async def _hold_keys(page: Any, a: Action) -> None:
    """同時按住多個鍵一段時間；不管中途發生什麼，最後一定放開（FR-005）。"""
    pressed: list[str] = []
    try:
        for key in a.args["keys"]:
            name = to_playwright_key(key)
            await page.keyboard.down(name)
            pressed.append(name)
        await asyncio.sleep(float(a.args["seconds"]))
    finally:
        for name in reversed(pressed):
            await page.keyboard.up(name)


async def _tap_keys(page: Any, a: Action) -> None:
    combo = to_playwright_combo(a.args["keys"])
    for _ in range(int(a.args.get("times", 1))):
        # 按住一小段時間再放開：Flash／Ruffle 這類遊戲每一格畫面（約 33ms）才讀一次按鍵狀態，瞬間按放會被漏掉
        await page.keyboard.press(combo, delay=TAP_HOLD_MS)
        await asyncio.sleep(TAP_GAP_SEC)


async def _hold_mouse(page: Any, a: Action) -> None:
    button = a.args.get("button", "left")
    await page.mouse.down(button=button)
    try:
        await asyncio.sleep(float(a.args["seconds"]))
    finally:
        await page.mouse.up(button=button)


_HANDLERS: dict[ActionKind, Handler] = {
    ActionKind.CLICK: _click,
    ActionKind.DOUBLE_CLICK: _double_click,
    ActionKind.RIGHT_CLICK: _right_click,
    ActionKind.DRAG: _drag,
    ActionKind.SCROLL: _scroll,
    ActionKind.TYPE_TEXT: _type_text,
    ActionKind.PRESS_KEYS: _press_keys,
    ActionKind.NAVIGATE: _navigate,
    ActionKind.GO_BACK: _go_back,
    ActionKind.CLICK_ELEMENT: _click_element,
    ActionKind.FILL_ELEMENT: _fill_element,
    ActionKind.WAIT: _wait,
    ActionKind.HOLD_KEYS: _hold_keys,
    ActionKind.TAP_KEYS: _tap_keys,
    ActionKind.HOLD_MOUSE: _hold_mouse,
}

SUPPORTED_KINDS = frozenset(_HANDLERS)
