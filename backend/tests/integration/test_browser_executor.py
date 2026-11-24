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


async def test_locator_is_recorded_for_element_actions(browser):
    table = await browser.elements()
    submit = by_name(table, "送出表單")
    assert submit.css == "#go"
    by_ref = await browser.execute(Action(ActionKind.CLICK_ELEMENT, {"ref": submit.ref}))
    assert by_ref.locator["role"] == "button" and by_ref.locator["name"] == "送出表單"
    assert by_ref.locator["css"] == "#go" and by_ref.locator["frame"] == [1280, 800]
    assert "locator" not in by_ref.data  # 定位資訊不回傳給模型
    by_point = await browser.execute(Action(ActionKind.CLICK, {"x": 200, "y": 215}))
    assert by_point.locator["name"] == "你的名字" and by_point.locator["point"] == [200, 215]
    assert (await browser.execute(Action(ActionKind.WAIT, {"seconds": 0}))).locator is None


async def test_ref_for_css(browser):
    ref = await browser.ref_for_css("#go")
    assert ref == "e0"
    assert (await browser.execute(Action(ActionKind.CLICK_ELEMENT, {"ref": ref}))).ok
    assert await out_text(browser) == "已點擊:"
    assert await browser.ref_for_css("button") is None  # 對到多個（含隱藏按鈕）→ 不採用
    assert await browser.ref_for_css(":::bad") is None


async def test_hold_tap_and_focus_box_on_game_page(browser, page_server):
    await browser.execute(Action(ActionKind.NAVIGATE, {"url": f"{page_server}/game.html"}))
    box = await browser.focus_box()
    assert box == {"x1": 200, "y1": 150, "x2": 840, "y2": 550}  # 最大的 canvas 就是遊戲畫面

    held = await browser.execute(Action(ActionKind.HOLD_KEYS, {"keys": ("right",), "seconds": 0.4}))
    assert held.ok
    events = await browser.page.evaluate("window.events")
    [up] = [e for e in events if e["type"] == "up"]
    assert up["key"] == "ArrowRight" and up["held"] >= 350

    await browser.page.evaluate("window.events = []")
    assert (await browser.execute(Action(ActionKind.TAP_KEYS, {"keys": ("a",), "times": 3}))).ok
    taps = await browser.page.evaluate("window.events")
    assert [e["type"] for e in taps] == ["down", "up"] * 3
    # 每次點按都要按住一小段時間，每格畫面才讀一次按鍵的遊戲（Flash／Ruffle）才不會漏掉
    assert all(e["held"] >= 40 for e in taps if e["type"] == "up")

    assert (await browser.execute(Action(ActionKind.HOLD_MOUSE, {"button": "left", "seconds": 0.3}))).ok
    assert await browser.page.evaluate("window.mouseHeld") >= 250


async def test_reveal_focus_scrolls_a_clipped_game_into_view(browser, page_server):
    await browser.execute(Action(ActionKind.NAVIGATE, {"url": f"{page_server}/game.html"}))
    assert await browser.reveal_focus() is False  # 已經完整可見，不捲動

    await browser.page.evaluate("document.getElementById('game').style.top = '600px'")  # 下緣超出 800px 視窗
    assert (await browser.focus_box())["y2"] == 800
    assert await browser.reveal_focus() is True
    box = await browser.focus_box()
    assert box["y2"] - box["y1"] == 400  # 整個遊戲畫面都看得到


async def test_focus_box_without_game_area(browser):
    assert await browser.focus_box() is None


async def test_errors_are_returned_not_raised(browser):
    unknown_ref = await browser.execute(Action(ActionKind.CLICK_ELEMENT, {"ref": "e999"}))
    assert not unknown_ref.ok and "e999" in unknown_ref.message
    unsupported = await browser.execute(Action(ActionKind.LOOK, {"dx": 10, "dy": 0}))
    assert not unsupported.ok
