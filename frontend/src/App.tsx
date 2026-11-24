import { useMemo, useState } from "react";
import { createApi } from "./api/client";
import { ConfirmDialog, HelpDialog } from "./components/Dialogs";
import { LiveView } from "./components/LiveView";
import { NewTaskForm } from "./components/NewTaskForm";
import { QuotaPanel } from "./components/QuotaPanel";
import { TaskDetailView } from "./components/TaskDetailView";
import { TaskHistory } from "./components/TaskHistory";
import { useLive } from "./hooks/useLive";
import { shortModel } from "./lib/format";
import { FINAL_STATUSES } from "./state/types";

const CONNECTION_TEXT = { connecting: "連線中", open: "已連線", closed: "連線中斷" } as const;

export function App({ token }: { token: string }) {
  const api = useMemo(() => createApi(token), [token]);
  const { state, start, stop, answerConfirm, helpDone, dismissNotice } = useLive(api, token);
  const [viewing, setViewing] = useState<string | null>(null);
  const busy = state.status !== null && !FINAL_STATUSES.includes(state.status);

  return (
    <div className="app">
      <header className="topbar">
        <span className="brand">Familiar</span>
        <span className="topbar-right">
          {state.currentModel && <span className="chip mono">{shortModel(state.currentModel)}</span>}
          <span className={`conn conn-${state.connection}`}>{CONNECTION_TEXT[state.connection]}</span>
        </span>
      </header>

      {state.notice && (
        <div className="notice" role="alert">
          <span>{state.notice}</span>
          <button className="btn-link" onClick={dismissNotice} aria-label="關閉訊息">
            關閉
          </button>
        </div>
      )}

      <main className="layout">
        <aside className="side">
          <NewTaskForm busy={busy} onStart={async (url, instruction) => {
            setViewing(null);
            return start(url, instruction);
          }} />
          <QuotaPanel quota={state.quota} currentModel={state.currentModel} />
          <TaskHistory api={api} refreshKey={`${state.taskId}:${state.status}`} selectedId={viewing} onSelect={setViewing} />
        </aside>
        <div className="main">
          {viewing && viewing !== state.taskId ? (
            <TaskDetailView api={api} taskId={viewing} onBack={() => setViewing(null)} />
          ) : (
            <LiveView state={state} onStop={stop} screenshotUrl={api.screenshotUrl} />
          )}
        </div>
      </main>

      <ConfirmDialog prompt={state.confirm} onAnswer={answerConfirm} />
      <HelpDialog prompt={state.help} onDone={helpDone} onStop={stop} />
    </div>
  );
}
