import { formatTime, shortModel } from "../lib/format";
import type { QuotaMap } from "../state/types";

const STATUS_TEXT = { ready: "可用", wait: "稍等", unavailable: "暫停" } as const;

export function QuotaPanel({ quota, currentModel }: { quota: QuotaMap; currentModel: string | null }) {
  const entries = Object.entries(quota);
  return (
    <section className="card" aria-label="模型額度">
      <h2>今日額度</h2>
      {entries.length === 0 ? (
        <p className="muted">讀取中…</p>
      ) : (
        <ul className="quota">
          {entries.map(([model, q]) => (
            <li key={model} className={model === currentModel ? "quota-current" : ""}>
              <span className="mono quota-name">{shortModel(model)}</span>
              <span className="quota-count">
                {q.day_requests}
                {q.rpd ? ` / ${q.rpd}` : ""}
              </span>
              <span className={`dot dot-${q.status}`} title={q.ready_at ? `恢復時間 ${formatTime(q.ready_at)}` : ""}>
                {STATUS_TEXT[q.status]}
              </span>
            </li>
          ))}
        </ul>
      )}
      <p className="muted small">額度在太平洋時間午夜重置（台灣時間下午 4 點，夏令時間下午 3 點）。</p>
    </section>
  );
}
