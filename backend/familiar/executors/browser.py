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
from familiar.executors.keys import to_playwright_combo

logger = logging.getLogger(__name__)

NAV_TIMEOUT_MS = 30_000
ELEMENT_TIMEOUT_MS = 5_000
SETTLE_TIMEOUT_MS = 3_000
SCROLL_PIXELS_PER_NOTCH = 100
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
}

SUPPORTED_KINDS = frozenset(_HANDLERS)
