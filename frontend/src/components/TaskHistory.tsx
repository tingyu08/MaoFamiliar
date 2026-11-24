import { useEffect, useState } from "react";
import type { Api } from "../api/client";
import { formatTime } from "../lib/format";
import type { TaskRecord } from "../state/types";
import { StatusBadge } from "./StatusBadge";

interface Props {
  api: Api;
  refreshKey: string;
  selectedId: string | null;
  onSelect: (id: string) => void;
}

export function TaskHistory({ api, refreshKey, selectedId, onSelect }: Props) {
  const [tasks, setTasks] = useState<TaskRecord[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    api
      .listTasks(15)
      .then((page) => !cancelled && (setTasks(page.items), setError(null)))
      .catch(() => !cancelled && setError("讀取歷史失敗"));
    return () => {
      cancelled = true;
    };
  }, [api, refreshKey]);

  return (
    <section className="card" aria-label="任務歷史">
      <h2>最近的任務</h2>
      {error && <p className="form-error">{error}</p>}
      {tasks.length === 0 && !error ? (
        <p className="muted">還沒有任務</p>
      ) : (
        <ul className="history">
          {tasks.map((task) => (
            <li key={task.id}>
              <button className={`history-item ${task.id === selectedId ? "selected" : ""}`} onClick={() => onSelect(task.id)}>
                <span className="history-text">{task.instruction}</span>
                <span className="history-meta">
                  <StatusBadge status={task.status} />
                  <span className="muted small">{formatTime(task.created_at)}</span>
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
