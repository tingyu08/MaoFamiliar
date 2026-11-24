import { useState, type FormEvent } from "react";
import { isHttpUrl } from "../lib/format";

const MAX_INSTRUCTION = 2000;

export type TaskMode = "ui" | "realtime";

interface Props {
  busy: boolean;
  onStart: (url: string, instruction: string, mode: TaskMode) => Promise<boolean>;
}

export function NewTaskForm({ busy, onStart }: Props) {
  const [url, setUrl] = useState("");
  const [instruction, setInstruction] = useState("");
  const [game, setGame] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    const trimmedUrl = url.trim();
    const trimmedInstruction = instruction.trim();
    if (!isHttpUrl(trimmedUrl)) return setError("網址要以 http:// 或 https:// 開頭");
    if (!trimmedInstruction) return setError("請描述想讓 AI 做的事");
    setError(null);
    setSubmitting(true);
    const ok = await onStart(trimmedUrl, trimmedInstruction, game ? "realtime" : "ui");
    setSubmitting(false);
    if (ok) setInstruction("");
  }

  return (
    <form className="card form" onSubmit={submit} aria-label="新任務">
      <h2>新任務</h2>
      <label>
        網址
        <input
          type="url"
          inputMode="url"
          placeholder="https://example.com"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          disabled={busy}
        />
      </label>
      <label>
        想讓 AI 做的事
        <textarea
          rows={4}
          maxLength={MAX_INSTRUCTION}
          placeholder="例如：找到最新一篇公告，告訴我標題和日期"
          value={instruction}
          onChange={(e) => setInstruction(e.target.value)}
          disabled={busy}
        />
      </label>
      <label className="check">
        <input type="checkbox" checked={game} onChange={(e) => setGame(e.target.checked)} disabled={busy} />
        <span>
          遊戲模式
          <span className="muted small">（可以按住按鍵；只看遊戲畫面判斷有沒有卡住；不能存成技能）</span>
        </span>
      </label>
      {error && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
      <button type="submit" className="btn btn-primary" disabled={busy || submitting}>
        {busy ? "有任務正在執行" : submitting ? "建立中…" : "開始"}
      </button>
    </form>
  );
}
