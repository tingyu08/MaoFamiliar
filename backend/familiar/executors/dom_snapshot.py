"""在頁面中找出可見、可互動的元素，加上 data-familiar-ref 標記並回傳元素表。"""

from __future__ import annotations

from typing import Any

from familiar.executors.elements import Element, ElementTable

MAX_ELEMENTS = 150
REF_ATTR = "data-familiar-ref"

# 注意：輸入框的 value 可能是使用者資料或密碼，絕不當作名稱回傳
_COLLECT_JS = """
([maxCount, refAttr]) => {
  document.querySelectorAll(`[${refAttr}]`).forEach(e => e.removeAttribute(refAttr));
  const selector = [
    'a[href]', 'button', 'input:not([type=hidden])', 'select', 'textarea', 'summary',
    '[role=button]', '[role=link]', '[role=checkbox]', '[role=tab]', '[role=menuitem]',
    '[role=option]', '[role=radio]', '[role=switch]', '[onclick]', '[contenteditable=""]',
    '[contenteditable=true]'
  ].join(',');
  const vw = window.innerWidth, vh = window.innerHeight;
  const roleOf = (el, tag, type) => {
    const explicit = el.getAttribute('role');
    if (explicit) return explicit;
    if (tag === 'a') return 'link';
    if (tag === 'button' || tag === 'summary') return 'button';
    if (tag === 'select') return 'combobox';
    if (tag === 'textarea') return 'textbox';
    if (tag === 'input') {
      if (type === 'checkbox' || type === 'radio') return type;
      if (['submit', 'button', 'reset', 'image'].includes(type)) return 'button';
      return 'textbox';
    }
    return tag;
  };
  const nameOf = (el, tag, type) => {
    const buttonValue = tag === 'input' && ['submit', 'button', 'reset'].includes(type) ? el.value : '';
    const text = tag === 'input' || tag === 'select' ? '' : el.innerText;
    const raw = el.getAttribute('aria-label') || text || buttonValue || el.getAttribute('placeholder')
      || el.getAttribute('title') || el.getAttribute('alt') || '';
    return raw.trim().replace(/\\s+/g, ' ').slice(0, 60);
  };
  const out = [];
  for (const el of document.querySelectorAll(selector)) {
    if (out.length >= maxCount) break;
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) continue;
    if (r.bottom < 0 || r.right < 0 || r.top > vh || r.left > vw) continue;
    const style = getComputedStyle(el);
    if (style.visibility === 'hidden' || style.display === 'none' || el.disabled) continue;
    const tag = el.tagName.toLowerCase();
    const type = (el.getAttribute('type') || '').toLowerCase();
    const ref = 'e' + (out.length + 1);
    el.setAttribute(refAttr, ref);
    out.push({
      ref, role: roleOf(el, tag, type), name: nameOf(el, tag, type),
      x1: Math.max(0, r.left), y1: Math.max(0, r.top), x2: Math.min(vw, r.right), y2: Math.min(vh, r.bottom),
      is_password: type === 'password'
    });
  }
  return out;
}
"""


def _element(raw: dict[str, Any]) -> Element:
    return Element(ref=str(raw["ref"]), role=str(raw["role"]), name=str(raw["name"]),
                   x1=float(raw["x1"]), y1=float(raw["y1"]), x2=float(raw["x2"]), y2=float(raw["y2"]),
                   is_password=bool(raw["is_password"]))


async def snapshot_elements(page: Any, max_count: int = MAX_ELEMENTS) -> ElementTable:
    raw = await page.evaluate(_COLLECT_JS, [max_count, REF_ATTR])
    return ElementTable(tuple(_element(item) for item in raw))


def ref_selector(ref: str) -> str:
    return f'[{REF_ATTR}="{ref}"]'
