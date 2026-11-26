// 顯示用的文字轉換

import type { ActionInfo, TaskStatus } from "../state/types";

export const STATUS_LABEL: Record<TaskStatus, string> = {
  pending: "準備中",
  running: "執行中",
  waiting_user: "等你回應",
  waiting_quota: "等待額度",
  succeeded: "完成",
  failed: "失敗",
  stopped: "已停止",
};

const KIND_LABEL: Record<string, string> = {
  click: "點擊",
  double_click: "雙擊",
  right_click: "右鍵",
  drag: "拖曳",
  scroll: "捲動",
  type_text: "輸入文字",
  press_keys: "按鍵",
  navigate: "前往網址",
  go_back: "上一頁",
  click_element: "點擊元素",
  fill_element: "填寫欄位",
  wait: "等待",
  zoom: "放大查看",
  ask_user: "請你協助",
  task_done: "完成任務",
  complete_subgoal: "完成子目標",
  revise_plan: "調整計畫",
  register_state: "記住畫面",
  save_note: "記下心得",
  use_skill: "使用技能",
  request_mode: "建議切換模式",
  hold_keys: "按住按鍵",
  tap_keys: "連按按鍵",
  hold_mouse: "按住滑鼠",
  set_reflex: "交給反射層",
};

export const kindLabel = (kind: string): string => KIND_LABEL[kind] ?? kind;

function detail(action: ActionInfo): string {
  const a = action.args;
  switch (action.kind) {
    case "click":
    case "double_click":
    case "right_click":
      return `(${a.x}, ${a.y})`;
    case "type_text":
    case "fill_element":
      return typeof a.text === "string" ? `「${a.text}」` : "";
    case "press_keys":
      return Array.isArray(a.keys) ? a.keys.join(" + ") : "";
    case "hold_keys":
      return Array.isArray(a.keys) ? `${a.keys.join(" + ")} ${a.seconds ?? ""} 秒` : "";
    case "tap_keys":
      return Array.isArray(a.keys) ? `${a.keys.join(" + ")} ×${a.times ?? 1}` : "";
    case "set_reflex":
      return Array.isArray(a.rules) ? `${a.rules.length} 條規則，${a.iterations ?? 20} 輪` : "";
    case "navigate":
      return String(a.url ?? "");
    case "scroll":
      return `${a.direction ?? ""} ×${a.amount ?? ""}`;
    case "wait":
      return `${a.seconds ?? 1} 秒`;
    default:
      return "";
  }
}

/** 例如「填寫欄位「小明」」，有說明時優先顯示模型寫的 intent。 */
export function describeAction(action: ActionInfo): { title: string; detail: string } {
  return { title: action.intent || kindLabel(action.kind), detail: [kindLabel(action.kind), detail(action)].filter(Boolean).join(" ") };
}

export function shortModel(model: string | null | undefined): string {
  if (!model) return "—";
  return model.replace(/^gemini-/, "").replace(/-/g, " ");
}

export function formatTime(iso: string | null | undefined): string {
  if (!iso) return "";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleString("zh-TW", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

export const REASON_LABEL: Record<string, string> = {
  high_risk: "AI 判斷這個動作可能無法復原（例如送出、付款、刪除）",
  model_safety: "Gemini 的安全機制要求確認",
};

export const reasonText = (reason: string): string => REASON_LABEL[reason] ?? reason;

export function isHttpUrl(value: string): boolean {
  try {
    const url = new URL(value);
    return (url.protocol === "http:" || url.protocol === "https:") && Boolean(url.hostname);
  } catch {
    return false;
  }
}
