import { describe, expect, it } from "vitest";
import { initialState, liveReducer } from "./reducer";
import type { LiveState, ServerEvent, TaskDetail } from "./types";

const ev = (event: ServerEvent) => ({ type: "event" as const, event });
const run = (events: ServerEvent[], start: LiveState = initialState) =>
  events.reduce((state, e) => liveReducer(state, ev(e)), start);

const started = { type: "task_started", task_id: "t1", target: "https://a.b", instruction: "找公告" };
const step = (index: number, ok = true) => ({
  type: "step", task_id: "t1", index, layer: "agent", model: "lite",
  action: { kind: "click", args: { x: 1 }, intent: "點擊" }, ok, message: ok ? "" : "失敗", thumbnail: `img${index}`,
});

describe("liveReducer", () => {
  it("does not mutate the previous state", () => {
    const frozen = Object.freeze({ ...initialState, steps: Object.freeze([]) as never });
    const next = liveReducer(frozen, ev(started));
    expect(next).not.toBe(frozen);
    expect(frozen.taskId).toBeNull();
  });

  it("starts a task and resets previous data", () => {
    const old = run([started, step(0), { type: "task_finished", task_id: "t1", status: "failed", error: "x", steps: 1 }]);
    const next = run([{ ...started, task_id: "t2" }], old);
    expect(next.taskId).toBe("t2");
    expect(next.status).toBe("running");
    expect(next.steps).toEqual([]);
    expect(next.result).toBeNull();
  });

  it("appends steps once and tracks the latest screenshot", () => {
    const state = run([started, step(0), step(1, false), step(1, false)]);
    expect(state.steps.map((s) => s.index)).toEqual([0, 1]);
    expect(state.steps[1].ok).toBe(false);
    expect(state.latestThumbnail).toBe("img1");
  });

  it("ignores steps from another task", () => {
    const state = run([started, { ...step(0), task_id: "other" }]);
    expect(state.steps).toEqual([]);
  });

  it("shows and clears a confirmation request", () => {
    const asking = run([started, {
      type: "confirm_request", task_id: "t1", request_id: "r1", reason: "high_risk", thumbnail: "t",
      action: { kind: "click", args: {}, intent: "購買", risk: "high" },
    }]);
    expect(asking.confirm?.requestId).toBe("r1");
    const answered = liveReducer(asking, { type: "prompt_answered" });
    expect(answered.confirm).toBeNull();
    const resumed = run([{ type: "task_status", task_id: "t1", status: "running" }], asking);
    expect(resumed.confirm).toBeNull();
    expect(resumed.status).toBe("running");
  });

  it("handles user help and waiting for quota", () => {
    const help = run([started, { type: "user_help_request", task_id: "t1", request_id: "h1", reason: "請登入" }]);
    expect(help.help).toEqual({ requestId: "h1", reason: "請登入" });
    const quota = run([{ type: "waiting_quota", task_id: "t1", ready_at: "2026-11-24T08:00:00Z" }], help);
    expect(quota.status).toBe("waiting_quota");
    expect(quota.waitingQuotaUntil).toBe("2026-11-24T08:00:00Z");
  });

  it("records quota updates and current model", () => {
    const state = run([started, {
      type: "quota_update", model: "gemini-3.5-flash-lite",
      quota: { "gemini-3.5-flash-lite": { day_requests: 3, rpd: 0, rpm: 0, tpm: 0, status: "ready", ready_at: null } },
    }]);
    expect(state.currentModel).toBe("gemini-3.5-flash-lite");
    expect(state.quota["gemini-3.5-flash-lite"].day_requests).toBe(3);
  });

  it("finishes a task and clears prompts", () => {
    const state = run([started, { type: "user_help_request", task_id: "t1", request_id: "h1", reason: "?" },
      { type: "task_finished", task_id: "t1", status: "succeeded", result_text: "完成", error: null, steps: 2 }]);
    expect(state.status).toBe("succeeded");
    expect(state.result).toEqual({ status: "succeeded", text: "完成", error: null, steps: 2 });
    expect(state.help).toBeNull();
  });

  it("restores pending prompts from hello", () => {
    const state = run([{
      type: "hello", current_task_id: "t1",
      pending: [{ type: "confirm_request", task_id: "t1", request_id: "r9", reason: "high_risk",
        action: { kind: "click", args: {}, intent: "送出" } }],
    }]);
    expect(state.taskId).toBe("t1");
    expect(state.confirm?.requestId).toBe("r9");
  });

  it("loads a running task from REST after reconnecting", () => {
    const detail: TaskDetail = {
      running: true,
      task: { id: "t1", environment: "browser", target: "https://a.b", instruction: "找公告", mode: "browser",
        status: "running", created_at: "", result_text: null, error_message: null, steps_count: 1,
        agent_calls: 1, finished_at: null },
      steps: [{ task_id: "t1", idx: 0, layer: "agent", model: "lite", action: { kind: "click", args: {}, intent: "點" },
        result: { ok: true, message: "" }, duration_ms: 10, tokens_in: 100 }],
    };
    const state = liveReducer(initialState, { type: "load_task", detail });
    expect(state.instruction).toBe("找公告");
    expect(state.steps).toHaveLength(1);
    expect(state.status).toBe("running");
  });

  it("keeps server error messages as a notice", () => {
    const state = run([{ type: "error", message: "訊息格式錯誤" }]);
    expect(state.notice).toBe("訊息格式錯誤");
    expect(liveReducer(state, { type: "dismiss_notice" }).notice).toBeNull();
  });

  it("tracks replay mode and self-healing", () => {
    const replay = run([{ ...started, mode: "replay", skill_id: "abc" }]);
    expect(replay.mode).toBe("replay");
    const healing = run([{ type: "skill_heal", task_id: "t1", failed_step: 1, reason: "找不到「送出」" }], replay);
    expect(healing.healing).toBe("找不到「送出」");
    expect(run([started], healing).healing).toBeNull();
  });

  it("tracks game mode", () => {
    expect(run([{ ...started, realtime: true }]).realtime).toBe(true);
    expect(run([started]).realtime).toBe(false);
  });

  it("tracks connection state", () => {
    expect(liveReducer(initialState, { type: "connection", value: "open" }).connection).toBe("open");
  });
});
