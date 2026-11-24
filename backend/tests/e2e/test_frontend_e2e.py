"""前端 E2E：真的後端＋建置好的前端＋兩個 headless Chromium（一個給 AI 操作、一個扮演使用者）。

驗收 PRD 的 UC1（網頁任務）、UC7（高風險確認）、UC8（額度用完換模型），以及重新整理後補發確認窗。
需要先建置前端：cd frontend; npm run build
"""

from __future__ import annotations

import socket
import sys
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect, sync_playwright

sys.path.insert(0, str(Path(__file__).parent))
from demo_server import FRONTEND_DIST, ScriptedFormModel, start_demo  # noqa: E402

pytestmark = pytest.mark.skipif(not (FRONTEND_DIST / "index.html").exists(), reason="前端尚未建置（npm run build）")

TIMEOUT_MS = 30_000
EXPECT_TIMEOUT_MS = 15_000  # expect() 預設只等 5 秒；加上完成檢查後一個任務約要 6 秒
expect.set_options(timeout=EXPECT_TIMEOUT_MS)  # page.set_default_timeout 管不到 expect()


class DailyQuotaError(Exception):
    status_code = 429

    def __init__(self):
        super().__init__("Rate limit exceeded (limit: 20 requests per day on Free Tier). Please retry in 30m0s")


class LiteExhaustedModel(ScriptedFormModel):
    """Flash-Lite 一開始就回報今日額度用完，之後都由下一個模型處理。"""

    async def create_turn(self, *, model, **kwargs):
        if model == "gemini-3.5-flash-lite":
            raise DailyQuotaError()
        return await super().create_turn(model=model, **kwargs)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def demo_factory(tmp_path):
    servers = []

    def make(model):
        demo = start_demo(tmp_path / f"data{len(servers)}", free_port(), model=model)
        servers.append(demo)
        return demo

    yield make
    for demo in servers:
        demo.stop()


@pytest.fixture
def page():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.set_default_timeout(TIMEOUT_MS)
        yield page
        browser.close()


def open_app(page: Page, demo) -> None:
    page.goto(f"{demo.base_url}/?token={demo.token}")
    expect(page.get_by_text("已連線")).to_be_visible()
    assert "token" not in page.url  # token 不留在網址列


def start_task(page: Page, demo, instruction: str = "在名字欄位輸入小明並送出表單") -> None:
    page.get_by_placeholder("https://example.com").fill(f"{demo.page_url}/form.html")
    page.get_by_placeholder("例如").fill(instruction)
    page.get_by_role("button", name="開始").click()


def test_uc1_web_task(demo_factory, page):
    demo = demo_factory(ScriptedFormModel(risky_submit=False))
    open_app(page, demo)
    start_task(page, demo)
    expect(page.get_by_role("status")).to_contain_text("已點擊:小明")
    expect(page.locator(".badge-succeeded").first).to_be_visible()
    steps = page.get_by_role("list", name="步驟紀錄").locator("li")
    expect(steps).to_have_count(3)
    expect(page.get_by_role("region", name="任務歷史")).to_contain_text("在名字欄位輸入小明並送出表單")


def test_uc7_confirm_approve(demo_factory, page):
    demo = demo_factory(ScriptedFormModel())
    open_app(page, demo)
    start_task(page, demo)
    dialog = page.get_by_role("alertdialog")
    expect(dialog).to_contain_text("按下送出表單")
    expect(dialog.locator("img")).to_be_visible()
    page.get_by_role("button", name="允許執行").click()
    expect(dialog).to_be_hidden()
    expect(page.get_by_role("status")).to_contain_text("已點擊:小明")
    expect(page.locator(".tag-risk")).to_have_count(1)


def test_uc7_confirm_deny(demo_factory, page):
    demo = demo_factory(ScriptedFormModel())
    open_app(page, demo)
    start_task(page, demo)
    page.get_by_role("button", name="拒絕").click()
    expect(page.locator(".step-fail")).to_contain_text("使用者拒絕")


def test_reload_restores_pending_confirmation(demo_factory, page):
    demo = demo_factory(ScriptedFormModel())
    open_app(page, demo)
    start_task(page, demo)
    expect(page.get_by_role("alertdialog")).to_be_visible()
    page.reload()  # token 已存在 sessionStorage，重新整理不用再帶 token
    expect(page.get_by_role("alertdialog")).to_contain_text("按下送出表單")
    page.get_by_role("button", name="允許執行").click()
    expect(page.get_by_role("status")).to_contain_text("已點擊:小明")


def test_uc8_quota_fallback_is_visible(demo_factory, page):
    demo = demo_factory(LiteExhaustedModel(risky_submit=False))
    open_app(page, demo)
    start_task(page, demo)
    expect(page.get_by_role("status")).to_contain_text("已點擊:小明")
    expect(page.locator(".topbar .chip")).to_have_text("3.6 flash")
    quota = page.get_by_role("region", name="模型額度")
    expect(quota.locator("li", has_text="3.5 flash lite")).to_contain_text("暫停")
    expect(quota.locator("li", has_text="3.6 flash").first).to_contain_text("3 / 20")


class AskForHelpModel:
    """第一輪就請使用者協助（例如需要登入）。"""

    async def create_turn(self, **_kwargs):
        from familiar.gemini.interactions import AgentTurn

        call = {"id": "h1", "name": "ask_user", "arguments": {"reason": "這個網站需要先登入", "intent": "請你登入"}}
        return AgentTurn(interaction_id="help-1", function_calls=(call,), text="")


def test_help_dialog_and_stop(demo_factory, page):
    demo = demo_factory(AskForHelpModel())
    open_app(page, demo)
    start_task(page, demo)
    dialog = page.get_by_role("dialog")
    expect(dialog).to_contain_text("這個網站需要先登入")
    expect(page.locator(".badge-waiting_user").first).to_be_visible()
    page.get_by_role("button", name="停止任務").click()
    expect(page.get_by_role("status")).to_contain_text("已停止")
    expect(page.get_by_role("button", name="開始")).to_be_enabled()


def test_without_token_shows_instructions(demo_factory, page):
    demo = demo_factory(ScriptedFormModel())
    page.goto(demo.base_url)
    expect(page.get_by_text("請用後端啟動時印出的網址開啟")).to_be_visible()
