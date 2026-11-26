"""在頁面中找出可見、可互動的元素，加上 data-familiar-ref 標記並回傳元素表；
也負責描述單一元素（給技能錄製用的定位資訊），以及用 CSS 找回元素。"""

from __future__ import annotations

from typing import Any

from familiar.executors.elements import Element, ElementTable

MAX_ELEMENTS = 150
REF_ATTR = "data-familiar-ref"
CSS_REF = "e0"  # 重播時用 CSS 找到的元素暫時使用的編號

# 共用的頁面內函式。注意：輸入框的 value 可能是使用者資料或密碼，絕不當作名稱回傳
_HELPERS = r"""
  const SELECTOR = [
    'a[href]', 'button', 'input:not([type=hidden])', 'select', 'textarea', 'summary',
    '[role=button]', '[role=link]', '[role=checkbox]', '[role=tab]', '[role=menuitem]',
    '[role=option]', '[role=radio]', '[role=switch]', '[onclick]', '[contenteditable=""]',
    '[contenteditable=true]'
  ].join(',');
  const typeOf = (el) => (el.getAttribute('type') || '').toLowerCase();
  const roleOf = (el) => {
    const tag = el.tagName.toLowerCase(), type = typeOf(el);
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
  const nameOf = (el) => {
    const tag = el.tagName.toLowerCase(), type = typeOf(el);
    const buttonValue = tag === 'input' && ['submit', 'button', 'reset'].includes(type) ? el.value : '';
    const text = tag === 'input' || tag === 'select' ? '' : el.innerText;
    const raw = el.getAttribute('aria-label') || text || buttonValue || el.getAttribute('placeholder')
      || el.getAttribute('title') || el.getAttribute('alt') || '';
    return raw.trim().replace(/\s+/g, ' ').slice(0, 60);
  };
  const cssPath = (el) => {
    if (el.id && document.querySelectorAll('#' + CSS.escape(el.id)).length === 1) return '#' + CSS.escape(el.id);
    const parts = [];
    for (let node = el; node && node.nodeType === 1 && node !== document.body && parts.length < 6; node = node.parentElement) {
      const tag = node.tagName.toLowerCase();
      const siblings = node.parentElement ? [...node.parentElement.children].filter(c => c.tagName === node.tagName) : [];
      parts.unshift(siblings.length > 1 ? `${tag}:nth-of-type(${siblings.indexOf(node) + 1})` : tag);
    }
    return parts.join(' > ');
  };
  const describe = (el) => {
    const r = el.getBoundingClientRect();
    const vw = window.innerWidth, vh = window.innerHeight;
    return {
      role: roleOf(el), name: nameOf(el), css: cssPath(el),
      x1: Math.max(0, r.left), y1: Math.max(0, r.top), x2: Math.min(vw, r.right), y2: Math.min(vh, r.bottom),
      is_password: typeOf(el) === 'password'
    };
  };
"""

_COLLECT_JS = "([maxCount, refAttr]) => {" + _HELPERS + r"""
  document.querySelectorAll(`[${refAttr}]`).forEach(e => e.removeAttribute(refAttr));
  const vw = window.innerWidth, vh = window.innerHeight;
  const out = [];
  for (const el of document.querySelectorAll(SELECTOR)) {
    if (out.length >= maxCount) break;
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) continue;
    if (r.bottom < 0 || r.right < 0 || r.top > vh || r.left > vw) continue;
    const style = getComputedStyle(el);
    if (style.visibility === 'hidden' || style.display === 'none' || el.disabled) continue;
    const ref = 'e' + (out.length + 1);
    el.setAttribute(refAttr, ref);
    out.push({ ref, ...describe(el) });
  }
  return out;
}
"""

_DESCRIBE_JS = "([mode, value, refAttr]) => {" + _HELPERS + r"""
  let el = mode === 'ref' ? document.querySelector(`[${refAttr}="${value}"]`)
                          : document.elementFromPoint(value[0], value[1]);
  if (!el) return null;
  if (mode === 'point') { const target = el.closest(SELECTOR); if (target) el = target; }
  return describe(el);
}
"""

_MARK_CSS_JS = r"""([css, refAttr, ref]) => {
  let matches;
  try { matches = document.querySelectorAll(css); } catch (e) { return false; }
  if (matches.length !== 1) return false;
  document.querySelectorAll(`[${refAttr}="${ref}"]`).forEach(e => e.removeAttribute(refAttr));
  matches[0].setAttribute(refAttr, ref);
  return true;
}"""


# 網站在快照之後重新渲染（換掉節點）時標記會消失：用快照時的 role＋名稱＋是否密碼欄，
# 找回「唯一」相符的可見元素重新標記；找不到或有多個就不猜
_REMARK_JS = "([ref, role, name, isPassword, refAttr]) => {" + _HELPERS + r"""
  if (document.querySelector(`[${refAttr}="${ref}"]`)) return 'present';
  if (!name) return 'missing';
  const matches = [...document.querySelectorAll(SELECTOR)].filter(el => {
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) return false;
    const style = getComputedStyle(el);
    if (style.visibility === 'hidden' || style.display === 'none' || el.disabled) return false;
    return roleOf(el) === role && nameOf(el) === name && (typeOf(el) === 'password') === isPassword;
  });
  if (matches.length !== 1) return 'missing';
  matches[0].setAttribute(refAttr, ref);
  return 'remarked';
}
"""


def _element(raw: dict[str, Any]) -> Element:
    return Element(ref=str(raw["ref"]), role=str(raw["role"]), name=str(raw["name"]),
                   x1=float(raw["x1"]), y1=float(raw["y1"]), x2=float(raw["x2"]), y2=float(raw["y2"]),
                   is_password=bool(raw["is_password"]), css=str(raw.get("css", "")))


async def snapshot_elements(page: Any, max_count: int = MAX_ELEMENTS) -> ElementTable:
    raw = await page.evaluate(_COLLECT_JS, [max_count, REF_ATTR])
    return ElementTable(tuple(_element(item) for item in raw))


async def describe_ref(page: Any, ref: str) -> dict[str, Any] | None:
    return await page.evaluate(_DESCRIBE_JS, ["ref", ref, REF_ATTR])


async def describe_point(page: Any, x: float, y: float) -> dict[str, Any] | None:
    return await page.evaluate(_DESCRIBE_JS, ["point", [x, y], REF_ATTR])


async def mark_css(page: Any, css: str) -> str | None:
    """CSS 只對到唯一一個元素時，替它標上 CSS_REF 並回傳；否則回傳 None。"""
    found = await page.evaluate(_MARK_CSS_JS, [css, REF_ATTR, CSS_REF])
    return CSS_REF if found else None


async def ensure_ref(page: Any, ref: str, element: Element | None) -> bool:
    """標記還在，或依快照的 role＋名稱找回唯一相符的元素並重新標記時回傳 True。"""
    role, name, is_password = (element.role, element.name, element.is_password) if element else ("", "", False)
    status = await page.evaluate(_REMARK_JS, [ref, role, name, is_password, REF_ATTR])
    return status != "missing"


def ref_selector(ref: str) -> str:
    return f'[{REF_ATTR}="{ref}"]'
