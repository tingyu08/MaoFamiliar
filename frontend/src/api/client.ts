// REST 客戶端：統一處理 token 與回應格式 {success, data, error, meta}

import type { QuotaMap, SkillMeta, TaskDetail, TaskRecord } from "../state/types";

export class ApiError extends Error {
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

interface Envelope<T> {
  success: boolean;
  data: T;
  error: string | null;
  meta?: { total: number; limit: number; offset: number };
}

export interface Page<T> {
  items: T;
  total: number;
}

export type FetchLike = (input: string, init?: RequestInit) => Promise<Response>;

export function createApi(token: string, fetcher: FetchLike = (i, init) => fetch(i, init), base = "") {
  async function request<T>(path: string, init: RequestInit = {}): Promise<Envelope<T>> {
    let response: Response;
    try {
      response = await fetcher(base + path, {
        ...init,
        headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json", ...init.headers },
      });
    } catch {
      throw new ApiError("連不到後端，請確認 Familiar 是否還在執行", 0);
    }
    let body: Envelope<T> | null = null;
    try {
      body = (await response.json()) as Envelope<T>;
    } catch {
      body = null;
    }
    if (!response.ok || !body?.success) {
      throw new ApiError(body?.error ?? `請求失敗（HTTP ${response.status}）`, response.status);
    }
    return body;
  }

  return {
    async createTask(url: string, instruction: string): Promise<TaskRecord> {
      return (await request<TaskRecord>("/api/tasks", { method: "POST", body: JSON.stringify({ url, instruction }) })).data;
    },
    async listTasks(limit = 20, offset = 0): Promise<Page<TaskRecord[]>> {
      const body = await request<TaskRecord[]>(`/api/tasks?limit=${limit}&offset=${offset}`);
      return { items: body.data, total: body.meta?.total ?? body.data.length };
    },
    async getTask(id: string): Promise<TaskDetail> {
      return (await request<TaskDetail>(`/api/tasks/${encodeURIComponent(id)}`)).data;
    },
    async stopTask(id: string): Promise<void> {
      await request(`/api/tasks/${encodeURIComponent(id)}/stop`, { method: "POST" });
    },
    async quota(): Promise<QuotaMap> {
      return (await request<QuotaMap>("/api/quota")).data;
    },
    async listSkills(): Promise<SkillMeta[]> {
      return (await request<SkillMeta[]>("/api/skills")).data;
    },
    async createSkill(taskId: string, name: string): Promise<SkillMeta> {
      return (await request<SkillMeta>("/api/skills", { method: "POST", body: JSON.stringify({ task_id: taskId, name }) })).data;
    },
    async runSkill(skillId: string): Promise<TaskRecord> {
      return (await request<TaskRecord>(`/api/skills/${encodeURIComponent(skillId)}/run`, { method: "POST" })).data;
    },
    async deleteSkill(skillId: string): Promise<void> {
      await request(`/api/skills/${encodeURIComponent(skillId)}`, { method: "DELETE" });
    },
    screenshotUrl(id: string, idx: number): string {
      return `${base}/api/tasks/${encodeURIComponent(id)}/steps/${idx}/screenshot?token=${encodeURIComponent(token)}`;
    },
  };
}

export type Api = ReturnType<typeof createApi>;
