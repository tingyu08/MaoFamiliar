import { useState, type FormEvent } from "react";
import type { Api } from "../api/client";
import { ApiError } from "../api/client";

const MAX_NAME = 60;

interface Props {
  api: Api;
  taskId: string;
  defaultName: string;
  onSaved: (name: string) => void;
}

/** 把成功的任務存成技能：之後可以一鍵重播，不必再呼叫 AI。 */
export function SaveSkillForm({ api, taskId, defaultName, onSaved }: Props) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState(defaultName.slice(0, MAX_NAME));
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    const trimmed = name.trim();
    if (!trimmed) return setError("請輸入技能名稱");
    setSaving(true);
    try {
      await api.createSkill(taskId, trimmed);
      setSaved(true);
      setOpen(false);
      onSaved(trimmed);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "存檔失敗");
    } finally {
      setSaving(false);
    }
  }

  if (saved) return <p className="muted small">已存成技能，可以在左邊的「技能」一鍵重播。</p>;
  if (!open) {
    return (
      <button className="btn" onClick={() => setOpen(true)}>
        存成技能
      </button>
    );
  }
  return (
    <form className="save-skill" onSubmit={submit} aria-label="存成技能">
      <input value={name} maxLength={MAX_NAME} onChange={(e) => setName(e.target.value)} aria-label="技能名稱" autoFocus />
      <button type="submit" className="btn btn-primary" disabled={saving}>
        {saving ? "儲存中…" : "儲存"}
      </button>
      <button type="button" className="btn" onClick={() => setOpen(false)}>
        取消
      </button>
      {error && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
    </form>
  );
}
