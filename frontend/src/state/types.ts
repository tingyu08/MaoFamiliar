// 與後端事件（SDD 5.5）對應的型別

export type TaskStatus =
  | "pending"
  | "running"
  | "waiting_user"
  | "waiting_quota"
  | "succeeded"
  | "failed"
  | "stopped";

export const FINAL_STATUSES: readonly TaskStatus[] = ["succeeded", "failed", "stopped"];

export interface ActionInfo {
  kind: string;
  args: Record<string, unknown>;
  intent: string;
  risk?: string;
}

export interface StepView {
  index: number;
  layer: string;
  model: string | null;
  action: ActionInfo;
  ok: boolean;
  message: string;
  thumbnail?: string;
}

export interface ConfirmPrompt {
  requestId: string;
  action: ActionInfo;
  reason: string;
  thumbnail?: string;
}

export interface HelpPrompt {
  requestId: string;
  reason: string;
}

export interface QuotaEntry {
  day_requests: number;
  rpd: number;
  rpm: number;
  tpm: number;
  status: "ready" | "wait" | "unavailable";
  ready_at: string | null;
}

export type QuotaMap = Record<string, QuotaEntry>;

export interface TaskResult {
  status: TaskStatus;
  text: string | null;
  error: string | null;
  steps: number;
}

export type ConnectionState = "connecting" | "open" | "closed";

export interface LiveState {
  connection: ConnectionState;
  taskId: string | null;
  mode: "agent" | "replay" | null;
  realtime: boolean; // 遊戲模式
  healing: string | null; // 技能重播失敗、AI 接手修復時的原因
  target: string | null;
  instruction: string | null;
  status: TaskStatus | null;
  steps: StepView[];
  latestThumbnail: string | null;
  confirm: ConfirmPrompt | null;
  help: HelpPrompt | null;
  waitingQuotaUntil: string | null;
  currentModel: string | null;
  quota: QuotaMap;
  result: TaskResult | null;
  notice: string | null;
}

export type ServerEvent = { type: string; [key: string]: unknown };

// 後端 GET /api/tasks/{id} 的資料
export interface TaskRecord {
  id: string;
  environment: string;
  target: string;
  instruction: string;
  mode: string;
  status: TaskStatus;
  created_at: string;
  result_text: string | null;
  error_message: string | null;
  steps_count: number;
  agent_calls: number;
  decision_calls?: number;
  skill_steps?: number;
  actor_steps?: number;
  finished_at: string | null;
  skill_id?: string | null;
}

export interface SkillMeta {
  skill_id: string;
  name: string;
  target_id: string;
  start_url: string;
  instruction: string;
  current_version: number;
  success_count: number;
  fail_count: number;
  consecutive_failures: number;
  needs_relearn: boolean;
  step_count?: number;
  updated_at: string;
}

export interface StepRecord {
  task_id: string;
  idx: number;
  layer: string;
  model: string | null;
  action: ActionInfo;
  result: { ok: boolean; message: string };
  duration_ms: number;
  tokens_in: number;
}

export interface TaskDetail {
  task: TaskRecord;
  steps: StepRecord[];
  running: boolean;
}
