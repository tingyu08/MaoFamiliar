// 即時任務狀態：純函式 reducer，事件進來 → 產生新的狀態物件（不修改舊的）

import type {
  ActionInfo,
  ConnectionState,
  LiveState,
  QuotaMap,
  ServerEvent,
  StepView,
  TaskDetail,
  TaskStatus,
} from "./types";

export type LiveAction =
  | { type: "event"; event: ServerEvent }
  | { type: "connection"; value: ConnectionState }
  | { type: "load_task"; detail: TaskDetail }
  | { type: "prompt_answered" }
  | { type: "dismiss_notice" };

export const initialState: LiveState = {
  connection: "connecting",
  taskId: null,
  mode: null,
  healing: null,
  target: null,
  instruction: null,
  status: null,
  steps: [],
  latestThumbnail: null,
  confirm: null,
  help: null,
  waitingQuotaUntil: null,
  currentModel: null,
  quota: {},
  result: null,
  notice: null,
};

const str = (value: unknown): string => (typeof value === "string" ? value : "");
const optionalStr = (value: unknown): string | null => (typeof value === "string" ? value : null);

function asAction(value: unknown): ActionInfo {
  const raw = (value ?? {}) as Partial<ActionInfo>;
  return { kind: str(raw.kind), args: raw.args ?? {}, intent: str(raw.intent), risk: raw.risk };
}

const clearPrompts = (state: LiveState): LiveState => ({ ...state, confirm: null, help: null, waitingQuotaUntil: null });

function isOtherTask(state: LiveState, event: ServerEvent): boolean {
  const taskId = optionalStr(event.task_id);
  return Boolean(taskId && state.taskId && taskId !== state.taskId);
}

function addStep(state: LiveState, event: ServerEvent): LiveState {
  const step: StepView = {
    index: Number(event.index),
    layer: str(event.layer),
    model: optionalStr(event.model),
    action: asAction(event.action),
    ok: Boolean(event.ok),
    message: str(event.message),
    thumbnail: optionalStr(event.thumbnail) ?? undefined,
  };
  if (state.steps.some((s) => s.index === step.index)) return state;
  return { ...state, steps: [...state.steps, step], latestThumbnail: step.thumbnail ?? state.latestThumbnail };
}

function applyEvent(state: LiveState, event: ServerEvent): LiveState {
  if (event.type !== "task_started" && event.type !== "hello" && isOtherTask(state, event)) return state;
  switch (event.type) {
    case "hello": {
      const base = { ...state, taskId: optionalStr(event.current_task_id) ?? state.taskId };
      const pending = Array.isArray(event.pending) ? (event.pending as ServerEvent[]) : [];
      return pending.reduce(applyEvent, base);
    }
    case "task_started":
      return {
        ...clearPrompts(initialState),
        connection: state.connection,
        quota: state.quota,
        currentModel: state.currentModel,
        taskId: str(event.task_id),
        target: str(event.target),
        instruction: str(event.instruction),
        mode: event.mode === "replay" ? "replay" : "agent",
        status: "running",
      };
    case "skill_heal":
      return { ...state, healing: str(event.reason) || "重播失敗" };
    case "task_status": {
      const status = str(event.status) as TaskStatus;
      return status === "running" ? { ...clearPrompts(state), status } : { ...state, status };
    }
    case "step":
      return addStep(state, event);
    case "quota_update":
      return { ...state, currentModel: optionalStr(event.model), quota: (event.quota ?? {}) as QuotaMap };
    case "confirm_request":
      return {
        ...state,
        status: "waiting_user",
        confirm: {
          requestId: str(event.request_id),
          action: asAction(event.action),
          reason: str(event.reason),
          thumbnail: optionalStr(event.thumbnail) ?? undefined,
        },
      };
    case "user_help_request":
      return { ...state, status: "waiting_user", help: { requestId: str(event.request_id), reason: str(event.reason) } };
    case "waiting_quota":
      return { ...state, status: "waiting_quota", waitingQuotaUntil: optionalStr(event.ready_at) };
    case "task_finished": {
      const status = str(event.status) as TaskStatus;
      return {
        ...clearPrompts(state),
        status,
        result: {
          status,
          text: optionalStr(event.result_text),
          error: optionalStr(event.error),
          steps: Number(event.steps ?? state.steps.length),
        },
      };
    }
    case "error":
      return { ...state, notice: str(event.message) || "發生錯誤" };
    default:
      return state;
  }
}

function loadTask(state: LiveState, detail: TaskDetail): LiveState {
  const steps: StepView[] = detail.steps.map((s) => ({
    index: s.idx,
    layer: s.layer,
    model: s.model,
    action: s.action,
    ok: s.result.ok,
    message: s.result.message,
  }));
  return {
    ...state,
    taskId: detail.task.id,
    mode: detail.task.mode === "replay" ? "replay" : "agent",
    target: detail.task.target,
    instruction: detail.task.instruction,
    status: detail.task.status,
    steps,
  };
}

export function liveReducer(state: LiveState, action: LiveAction): LiveState {
  switch (action.type) {
    case "event":
      return applyEvent(state, action.event);
    case "connection":
      return { ...state, connection: action.value };
    case "load_task":
      return loadTask(state, action.detail);
    case "prompt_answered":
      return { ...state, confirm: null, help: null };
    case "dismiss_notice":
      return { ...state, notice: null };
  }
}
