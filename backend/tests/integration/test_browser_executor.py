"""BrowserExecutor 整合測試：真的開 Chromium（headless）操作本機測試網頁。"""

from familiar.core.models import Action, ActionKind, Environment, Frame


async def out_text(browser) -> str:
    return await browser.page.locator("#out").inner_text()


def by_name(table, name):
    return next(e for e in table.elements if e.name == name)


async def test_screenshot_frame_matches_viewport(browser):
    shot = await browser.screenshot()
    assert shot.frame == Frame(1280, 800)
    assert shot.png.startswith(b"\x89PNG")
    assert browser.environment is Environment.BROWSER


async def test_element_table(browser):
    table = await browser.elements()
    names = {e.name: e for e in table.elements}
    assert names["送出表單"].role == "button"
    assert names["你的名字"].role == "textbox"
    assert names["前往第二頁"].role == "link"
    assert "看不到的按鈕" not in names
    password = by_name(table, "密碼")
    assert password.is_password
    assert "topsecret" not in table.to_text()  # 密碼值絕不能外洩給模型
    assert table.to_text().startswith("e1 ")


async def test_click_by_pixel(browser):
    result = await browser.execute(Action(ActionKind.CLICK, {"x": 180, "y": 125}))
    assert result.ok
    assert await out_text(browser) == "已點擊:"


async def test_fill_and_click_elements(browser):
    table = await browser.elements()
    await browser.execute(Action(ActionKind.FILL_ELEMENT, {"ref": by_name(table, "你的名字").ref, "text": "小明"}))
    result = await browser.execute(Action(ActionKind.CLICK_ELEMENT, {"ref": by_name(table, "送出表單").ref}))
    assert result.ok
    assert await out_text(browser) == "已點擊:小明"


async def test_type_text_into_focused_input(browser):
    await browser.execute(Action(ActionKind.CLICK, {"x": 200, "y": 215}))
    await browser.execute(Action(ActionKind.TYPE_TEXT, {"text": "阿華", "press_enter": False}))
    assert await browser.page.locator("#name").input_value() == "阿華"


async def test_password_detection(browser):
    table = await browser.elements()
    fill_pw = Action(ActionKind.FILL_ELEMENT, {"ref": by_name(table, "密碼").ref, "text": "x"})
    fill_name = Action(ActionKind.FILL_ELEMENT, {"ref": by_name(table, "你的名字").ref, "text": "x"})
    assert await browser.is_password_target(fill_pw)
    assert not await browser.is_password_target(fill_name)
    await browser.execute(Action(ActionKind.CLICK, {"x": 200, "y": 275}))  # 聚焦密碼欄
    assert await browser.is_password_target(Action(ActionKind.TYPE_TEXT, {"text": "x"}))


async def test_press_keys(browser):
    result = await browser.execute(Action(ActionKind.PRESS_KEYS, {"keys": ("ctrl", "k")}))
    assert result.ok
    assert await out_text(browser) == "快捷鍵 Ctrl+K"


async def test_navigate_and_back(browser, page_server):
    await browser.execute(Action(ActionKind.NAVIGATE, {"url": f"{page_server}/second.html"}))
    info = await browser.context_info()
    assert info["url"].endswith("/second.html") and info["title"] == "第二頁"
    await browser.execute(Action(ActionKind.GO_BACK, {}))
    assert (await browser.context_info())["url"].endswith("/form.html")


async def test_scroll_and_wait(browser):
    assert (await browser.execute(Action(ActionKind.SCROLL, {"x": 600, "y": 400, "direction": "down",
                                                            "amount": 2}))).ok
    assert (await browser.execute(Action(ActionKind.WAIT, {"seconds": 0}))).ok


async def test_errors_are_returned_not_raised(browser):
    unknown_ref = await browser.execute(Action(ActionKind.CLICK_ELEMENT, {"ref": "e999"}))
    assert not unknown_ref.ok and "e999" in unknown_ref.message
    unsupported = await browser.execute(Action(ActionKind.HOLD_KEYS, {"keys": ("w",), "seconds": 1}))
    assert not unsupported.ok
