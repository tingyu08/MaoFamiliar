import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { describeAction, isHttpUrl, shortModel } from "../lib/format";
import { initialState } from "../state/reducer";
import { ConfirmDialog, HelpDialog } from "./Dialogs";
import { LiveView } from "./LiveView";
import { NewTaskForm } from "./NewTaskForm";
import { QuotaPanel } from "./QuotaPanel";
import { SaveSkillForm } from "./SaveSkillForm";
import { SkillsPanel } from "./SkillsPanel";
import { StepLog } from "./StepLog";

describe("format", () => {
  it("describes actions in Chinese", () => {
    expect(describeAction({ kind: "fill_element", args: { text: "小明" }, intent: "" })).toEqual({
      title: "填寫欄位", detail: "填寫欄位 「小明」",
    });
    expect(describeAction({ kind: "press_keys", args: { keys: ["ctrl", "s"] }, intent: "存檔" }).detail).toBe("按鍵 ctrl + s");
    expect(shortModel("gemini-3.5-flash-lite")).toBe("3.5 flash lite");
    expect(isHttpUrl("javascript:alert(1)")).toBe(false);
    expect(isHttpUrl("https://example.com")).toBe(true);
  });
});

describe("NewTaskForm", () => {
  it("validates before starting", async () => {
    const onStart = vi.fn(async () => true);
    render(<NewTaskForm busy={false} onStart={onStart} />);
    fireEvent.click(screen.getByRole("button", { name: "開始" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("http");
    fireEvent.change(screen.getByPlaceholderText("https://example.com"), { target: { value: "https://a.b" } });
    fireEvent.click(screen.getByRole("button", { name: "開始" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("描述");
    fireEvent.change(screen.getByPlaceholderText(/例如/), { target: { value: " 找公告 " } });
    fireEvent.click(screen.getByRole("button", { name: "開始" }));
    await waitFor(() => expect(onStart).toHaveBeenCalledWith("https://a.b", "找公告", "ui"));
    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.change(screen.getByPlaceholderText(/例如/), { target: { value: "玩第一關" } });
    fireEvent.click(screen.getByRole("button", { name: "開始" }));
    await waitFor(() => expect(onStart).toHaveBeenCalledWith("https://a.b", "玩第一關", "realtime"));
  });

  it("is disabled while a task runs", () => {
    render(<NewTaskForm busy onStart={vi.fn()} />);
    expect(screen.getByRole("button", { name: "有任務正在執行" })).toBeDisabled();
  });
});

describe("dialogs", () => {
  it("confirm dialog answers", () => {
    const onAnswer = vi.fn();
    render(<ConfirmDialog onAnswer={onAnswer}
      prompt={{ requestId: "r", reason: "high_risk", action: { kind: "click", args: { x: 1, y: 2 }, intent: "送出訂單" } }} />);
    expect(screen.getByRole("alertdialog")).toHaveTextContent("送出訂單");
    expect(screen.getByRole("alertdialog")).toHaveTextContent("無法復原");
    fireEvent.click(screen.getByRole("button", { name: "拒絕" }));
    fireEvent.click(screen.getByRole("button", { name: "允許執行" }));
    expect(onAnswer.mock.calls).toEqual([[false], [true]]);
  });

  it("help dialog", () => {
    const onDone = vi.fn();
    const onStop = vi.fn();
    render(<HelpDialog prompt={{ requestId: "h", reason: "請登入" }} onDone={onDone} onStop={onStop} />);
    fireEvent.click(screen.getByRole("button", { name: "我處理好了" }));
    fireEvent.click(screen.getByRole("button", { name: "停止任務" }));
    expect(onDone).toHaveBeenCalled();
    expect(onStop).toHaveBeenCalled();
  });

  it("renders nothing without a prompt", () => {
    const { container } = render(<ConfirmDialog prompt={null} onAnswer={vi.fn()} />);
    expect(container).toBeEmptyDOMElement();
  });
});

describe("LiveView", () => {
  it("shows an empty state, then a running task with stop", () => {
    const onStop = vi.fn();
    const { rerender } = render(<LiveView state={initialState} onStop={onStop} screenshotUrl={() => ""} />);
    expect(screen.getByText(/AI 開始操作後/)).toBeInTheDocument();
    rerender(<LiveView onStop={onStop} screenshotUrl={() => ""}
      state={{ ...initialState, taskId: "t1", instruction: "找公告", status: "waiting_quota", waitingQuotaUntil: null }} />);
    expect(screen.getByRole("status")).toHaveTextContent("額度暫時用完");
    fireEvent.click(screen.getByRole("button", { name: "停止" }));
    expect(onStop).toHaveBeenCalled();
  });

  it("shows the result when finished", () => {
    render(<LiveView onStop={vi.fn()} screenshotUrl={() => ""} state={{
      ...initialState, taskId: "t1", instruction: "x", status: "succeeded",
      result: { status: "succeeded", text: "已點擊:小明", error: null, steps: 3 },
    }} />);
    expect(screen.getByRole("status")).toHaveTextContent("已點擊:小明");
    expect(screen.queryByRole("button", { name: "停止" })).toBeNull();
  });
});

describe("StepLog layers", () => {
  it("labels replay, decisions and agent steps", () => {
    render(<StepLog steps={[
      { index: 0, layer: "skill", model: null, action: { kind: "click_element", args: {}, intent: "按送出" }, ok: true, message: "定位方式：CSS" },
      { index: 1, layer: "actor", model: "gemini-decision-1", action: { kind: "click_element", args: {}, intent: "點擊「說明」" }, ok: true, message: "" },
      { index: 2, layer: "agent", model: "gemini-3.5-flash-lite", action: { kind: "task_done", args: {}, intent: "完成" }, ok: true, message: "" },
    ]} />);
    expect(screen.getByText("重播")).toBeInTheDocument();
    expect(screen.getByText("Decisions")).toBeInTheDocument();
    expect(screen.getByText("3.5 flash lite")).toBeInTheDocument();
    expect(screen.getByText("定位方式：CSS")).toBeInTheDocument();
  });

  it("labels reflex key presses and shows the reflex report", () => {
    render(<StepLog steps={[
      { index: 0, layer: "reflex", model: "gemini-decision-1", action: { kind: "tap_keys", args: { keys: ["a"], times: 3 }, intent: "反射：attack" }, ok: true, message: "" },
      { index: 1, layer: "agent", model: "gemini-3.5-flash-lite", action: { kind: "set_reflex", args: { rules: [{}, {}], iterations: 5 }, intent: "交給反射層" }, ok: true, message: "反射層跑了 1 輪" },
    ]} />);
    expect(screen.getByText("反射")).toBeInTheDocument();
    expect(screen.getByText("連按按鍵 a ×3")).toBeInTheDocument();
    expect(screen.getByText("交給反射層 2 條規則，5 輪")).toBeInTheDocument();
    expect(screen.getByText("反射層跑了 1 輪")).toBeInTheDocument();
  });
});

describe("SaveSkillForm", () => {
  it("saves with the given name", async () => {
    const createSkill = vi.fn(async () => ({}) as never);
    const onSaved = vi.fn();
    render(<SaveSkillForm api={{ createSkill } as never} taskId="t1" defaultName="送出表單" onSaved={onSaved} />);
    fireEvent.click(screen.getByRole("button", { name: "存成技能" }));
    fireEvent.change(screen.getByLabelText("技能名稱"), { target: { value: " 每日簽到 " } });
    fireEvent.click(screen.getByRole("button", { name: "儲存" }));
    await waitFor(() => expect(onSaved).toHaveBeenCalledWith("每日簽到"));
    expect(createSkill).toHaveBeenCalledWith("t1", "每日簽到");
    expect(screen.getByText(/已存成技能/)).toBeInTheDocument();
  });

  it("shows server errors", async () => {
    const { ApiError } = await import("../api/client");
    const createSkill = vi.fn(async () => { throw new ApiError("這個任務中途請你協助過", 400); });
    render(<SaveSkillForm api={{ createSkill } as never} taskId="t1" defaultName="x" onSaved={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "存成技能" }));
    fireEvent.click(screen.getByRole("button", { name: "儲存" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("協助過");
  });
});

describe("SkillsPanel", () => {
  it("lists skills and runs one", async () => {
    const onRun = vi.fn(async () => undefined);
    const api = { listSkills: vi.fn(async () => [{ skill_id: "s1", name: "每日簽到", current_version: 2,
      success_count: 3, fail_count: 1, needs_relearn: true }]) };
    render(<SkillsPanel api={api as never} refreshKey="a" busy={false} onRun={onRun} onError={vi.fn()} />);
    expect(await screen.findByText("每日簽到")).toBeInTheDocument();
    expect(screen.getByText("需要重新學習")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "重播" }));
    expect(onRun).toHaveBeenCalledWith("s1");
  });
});

describe("QuotaPanel", () => {
  it("lists models and highlights the current one", () => {
    render(<QuotaPanel currentModel="gemini-3.6-flash" quota={{
      "gemini-3.6-flash": { day_requests: 4, rpd: 20, rpm: 5, tpm: 0, status: "ready", ready_at: null },
      "gemini-4-hydrogen": { day_requests: 20, rpd: 20, rpm: 0, tpm: 0, status: "unavailable", ready_at: "2026-11-24T08:00:00Z" },
    }} />);
    expect(screen.getByText("4 / 20")).toBeInTheDocument();
    expect(screen.getByText("暫停")).toBeInTheDocument();
    expect(screen.getByText("3.6 flash").closest("li")).toHaveClass("quota-current");
  });
});
