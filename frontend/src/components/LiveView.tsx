import { FINAL_STATUSES, type LiveState } from "../state/types";
import { formatTime } from "../lib/format";
import { StatusBadge } from "./StatusBadge";
import { StepLog } from "./StepLog";

interface Props {
  state: LiveState;
  onStop: () => void;
  screenshotUrl: (taskId: string, index: number) => string;
}

export function LiveView({ state, onStop, screenshotUrl }: Props) {
  const running = state.status !== null && !FINAL_STATUSES.includes(state.status);
  if (!state.taskId) {
    return (
      <section className="card live empty-live" aria-label="即時畫面">
        <h2>即時畫面</h2>
        <p className="muted">在左邊輸入網址和想做的事，AI 開始操作後畫面會出現在這裡。</p>
      </section>
    );
  }
  const taskId = state.taskId;
  return (
    <section className="card live" aria-label="即時畫面">
      <header className="live-head">
        <div className="live-title">
          <StatusBadge status={state.status} />
          <h2 title={state.instruction ?? ""}>{state.instruction}</h2>
        </div>
        {running && (
          <button className="btn btn-danger" onClick={onStop}>
            停止
          </button>
        )}
      </header>
      {state.target && <p className="target mono">{state.target}</p>}

      {state.status === "waiting_quota" && (
        <div className="banner banner-warn" role="status">
          所有模型的額度暫時用完，預計 {formatTime(state.waitingQuotaUntil) || "稍後"} 恢復後自動繼續。
        </div>
      )}
      {state.result && (
        <div className={`banner ${state.result.status === "succeeded" ? "banner-ok" : "banner-error"}`} role="status">
          <strong>{state.result.status === "succeeded" ? "結果" : state.result.status === "stopped" ? "已停止" : "失敗"}</strong>
          <p>{state.result.text ?? state.result.error ?? ""}</p>
        </div>
      )}

      <div className="live-grid">
        <figure className="screen">
          {state.latestThumbnail ? (
            <img src={`data:image/jpeg;base64,${state.latestThumbnail}`} alt="AI 最近看到的畫面" />
          ) : (
            <div className="screen-placeholder muted">{running ? "等待第一張畫面…" : "沒有畫面"}</div>
          )}
          <figcaption className="muted">AI 最近看到的畫面</figcaption>
        </figure>
        <div className="live-steps">
          <h3>步驟（最新在上）</h3>
          <StepLog steps={state.steps} screenshotUrl={(i) => screenshotUrl(taskId, i)} />
        </div>
      </div>
    </section>
  );
}
