import { useEffect, useState } from "react";
import type { Api } from "../api/client";
import { ApiError } from "../api/client";
import type { SkillMeta } from "../state/types";

interface Props {
  api: Api;
  refreshKey: string;
  busy: boolean;
  onRun: (skillId: string) => Promise<void>;
  onError: (message: string) => void;
}

export function SkillsPanel({ api, refreshKey, busy, onRun, onError }: Props) {
  const [skills, setSkills] = useState<SkillMeta[]>([]);
  const [version, setVersion] = useState(0);

  useEffect(() => {
    let cancelled = false;
    api
      .listSkills()
      .then((items) => !cancelled && setSkills(items))
      .catch(() => !cancelled && setSkills([]));
    return () => {
      cancelled = true;
    };
  }, [api, refreshKey, version]);

  async function remove(skill: SkillMeta) {
    if (!window.confirm(`確定要刪除技能「${skill.name}」嗎？這個動作無法復原。`)) return;
    try {
      await api.deleteSkill(skill.skill_id);
      setVersion((v) => v + 1);
    } catch (e) {
      onError(e instanceof ApiError ? e.message : "刪除失敗");
    }
  }

  return (
    <section className="card" aria-label="技能">
      <h2>技能</h2>
      {skills.length === 0 ? (
        <p className="muted small">任務成功後按「存成技能」，之後就能一鍵重播，不必再花 AI 額度。</p>
      ) : (
        <ul className="skills">
          {skills.map((skill) => (
            <li key={skill.skill_id} className="skill">
              <div className="skill-text">
                <span className="skill-name">{skill.name}</span>
                <span className="muted small">
                  第 {skill.current_version} 版 · 成功 {skill.success_count} · 失敗 {skill.fail_count}
                  {skill.needs_relearn && <span className="tag tag-risk">需要重新學習</span>}
                </span>
              </div>
              <div className="skill-buttons">
                <button className="btn btn-primary btn-small" disabled={busy} onClick={() => onRun(skill.skill_id)}>
                  重播
                </button>
                <button className="btn btn-small" onClick={() => remove(skill)} aria-label={`刪除 ${skill.name}`}>
                  刪除
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
