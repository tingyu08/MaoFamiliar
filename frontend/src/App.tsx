import { useMemo, useState } from "react";
import { createApi } from "./api/client";
import { ConfirmDialog, HelpDialog } from "./components/Dialogs";
import { LiveView } from "./components/LiveView";
import { NewTaskForm } from "./components/NewTaskForm";
import { QuotaPanel } from "./components/QuotaPanel";
import { SaveSkillForm } from "./components/SaveSkillForm";
import { SkillsPanel } from "./components/SkillsPanel";
import { TaskDetailView } from "./components/TaskDetailView";
import { TaskHistory } from "./components/TaskHistory";
import { useLive } from "./hooks/useLive";
import { shortModel } from "./lib/format";
import { FINAL_STATUSES } from "./state/types";

const CONNECTION_TEXT = { connecting: "連線中", open: "已連線", closed: "連線中斷" } as const;

export function App({ token }: { token: string }) {
  const api = useMemo(() => createApi(token), [token]);
  const { state, start, stop, runSkill, answerConfirm, helpDone, dismissNotice, showNotice } = useLive(api, token);
  const [viewing, setViewing] = useState<string | null>(null);
  const [skillsVersion, setSkillsVersion] = useState(0);
  const busy = state.status !== null && !FINAL_STATUSES.includes(state.status);
  const refreshKey = `${state.taskId}:${state.status}`;
  const onSkillSaved = () => setSkillsVersion((v) => v + 1);

  const canSave = state.taskId && state.result?.status === "succeeded" && state.mode === "agent" && !state.realtime;
  const resultActions = canSave ? (
    <SaveSkillForm key={state.taskId} api={api} taskId={state.taskId!} defaultName={state.instruction ?? ""}
      onSaved={onSkillSaved} />
  ) : undefined;

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
          <NewTaskForm busy={busy} onStart={async (url, instruction, mode) => {
            setViewing(null);
            return start(url, instruction, mode);
          }} />
          <SkillsPanel api={api} busy={busy} refreshKey={`${refreshKey}:${skillsVersion}`} onError={showNotice}
            onRun={async (skillId) => {
              setViewing(null);
              await runSkill(skillId);
            }} />
          <QuotaPanel quota={state.quota} currentModel={state.currentModel} />
          <TaskHistory api={api} refreshKey={refreshKey} selectedId={viewing} onSelect={setViewing} />
        </aside>
        <div className="main">
          {viewing && viewing !== state.taskId ? (
            <TaskDetailView api={api} taskId={viewing} onBack={() => setViewing(null)} onSkillSaved={onSkillSaved} />
          ) : (
            <LiveView state={state} onStop={stop} screenshotUrl={api.screenshotUrl} resultActions={resultActions} />
          )}
        </div>
      </main>

      <ConfirmDialog prompt={state.confirm} onAnswer={answerConfirm} />
      <HelpDialog prompt={state.help} onDone={helpDone} onStop={stop} />
    </div>
  );
}
