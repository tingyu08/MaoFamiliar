import { useEffect, useState } from "react";
import type { Api } from "../api/client";
import { formatTime } from "../lib/format";
import type { StepView, TaskDetail } from "../state/types";
import { StatusBadge } from "./StatusBadge";
import { StepLog } from "./StepLog";

export function TaskDetailView({ api, taskId, onBack }: { api: Api; taskId: string; onBack: () => void }) {
  const [detail, setDetail] = useState<TaskDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setDetail(null);
    api
      .getTask(taskId)
      .then((d) => !cancelled && setDetail(d))
      .catch(() => !cancelled && setError("讀取任務失敗"));
    return () => {
      cancelled = true;
    };
  }, [api, taskId]);

  const steps: StepView[] = (detail?.steps ?? []).map((s) => ({
    index: s.idx, layer: s.layer, model: s.model, action: s.action, ok: s.result.ok, message: s.result.message,
  }));
  const task = detail?.task;
  return (
    <section className="card live" aria-label="任務詳情">
      <header className="live-head">
        <div className="live-title">
          {task && <StatusBadge status={task.status} />}
          <h2>{task?.instruction ?? "讀取中…"}</h2>
        </div>
        <button className="btn" onClick={onBack}>
          回到即時畫面
        </button>
      </header>
      {error && <p className="form-error">{error}</p>}
      {task && (
        <>
          <p className="target mono">{task.target}</p>
          <p className="muted small">
            {formatTime(task.created_at)} · {task.steps_count} 步 · 呼叫模型 {task.agent_calls} 次
          </p>
          {(task.result_text || task.error_message) && (
            <div className={`banner ${task.status === "succeeded" ? "banner-ok" : "banner-error"}`}>
              <p>{task.result_text ?? task.error_message}</p>
            </div>
          )}
          <StepLog steps={steps} screenshotUrl={(i) => api.screenshotUrl(task.id, i)} />
        </>
      )}
    </section>
  );
}
