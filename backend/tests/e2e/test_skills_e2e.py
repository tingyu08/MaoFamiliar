"""第 4 週 E2E：第 3 層（Decisions 選候選）、存成技能、重播、網頁改版後自我修復。"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

sys.path.insert(0, str(Path(__file__).parent))
from demo_server import FRONTEND_DIST, PAGES, FormDecisions, ScriptedFormModel, start_demo  # noqa: E402
from test_frontend_e2e import free_port, open_app, page, start_task  # noqa: E402,F401

pytestmark = pytest.mark.skipif(not (FRONTEND_DIST / "index.html").exists(), reason="前端尚未建置（npm run build）")


def save_skill(page: Page, name: str) -> None:
    page.get_by_role("button", name="存成技能").click()
    page.get_by_label("技能名稱").fill(name)
    page.get_by_role("button", name="儲存").click()
    expect(page.get_by_text("已存成技能")).to_be_visible()
    expect(page.get_by_role("region", name="技能")).to_contain_text(name)


def replay(page: Page) -> None:
    page.get_by_role("region", name="技能").get_by_role("button", name="重播").click()


def test_layer3_then_record_and_replay_without_ai(tmp_path, page):
    model = ScriptedFormModel(risky_submit=False)
    decisions = FormDecisions()
    demo = start_demo(tmp_path / "data", free_port(), model=model, decisions=decisions)
    try:
        open_app(page, demo)
        start_task(page, demo)
        expect(page.get_by_role("status")).to_contain_text("已點擊:小明")
        steps = page.get_by_role("list", name="步驟紀錄")
        expect(steps.locator(".layer-actor")).to_have_count(1)  # 「送出表單」是第 3 層按的
        turns_before = model.turns

        save_skill(page, "填名字並送出")
        replay(page)
        expect(page.get_by_role("status")).to_contain_text("技能重播完成")
        expect(steps.locator(".layer-skill")).to_have_count(2)
        assert model.turns == turns_before  # 重播沒有呼叫代理人模型
    finally:
        demo.stop()


def test_replay_heals_after_page_changes(tmp_path, page):
    pages = tmp_path / "pages"
    shutil.copytree(PAGES, pages)
    model = ScriptedFormModel(risky_submit=False, heal_button="提交表單")
    demo = start_demo(tmp_path / "data", free_port(), model=model, pages_dir=pages)
    try:
        open_app(page, demo)
        start_task(page, demo)
        expect(page.get_by_role("status")).to_contain_text("已點擊:小明")
        save_skill(page, "送出表單技能")

        # 網站改版：按鈕改名、換 id、換位置 → 名稱、CSS、座標都對不上
        form = pages / "form.html"
        changed = (form.read_text(encoding="utf-8").replace("送出表單", "提交表單").replace('id="go"', 'id="submit"')
                   .replace("#go {", "#submit {").replace("left: 100px; top: 100px;", "left: 700px; top: 400px;"))
        form.write_text(changed, encoding="utf-8")

        replay(page)
        # 先等重播開始，否則會比對到上一個任務留下的結果橫幅
        expect(page.get_by_role("status")).to_contain_text("AI 正在接手修復")
        expect(page.get_by_role("status")).to_contain_text("第 2 版")
        expect(page.get_by_role("status")).to_contain_text("已點擊:小明")
        steps = page.get_by_role("list", name="步驟紀錄")
        expect(steps.locator(".step-fail").first).to_contain_text("找不到「送出表單」")
        expect(page.get_by_role("region", name="技能")).to_contain_text("第 2 版")

        replay(page)  # 新版本應該能直接重播成功
        expect(page.get_by_role("status")).to_contain_text("技能重播完成")
    finally:
        demo.stop()
