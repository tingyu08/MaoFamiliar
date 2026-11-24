import { useEffect, useRef } from "react";
import { describeAction, reasonText } from "../lib/format";
import type { ConfirmPrompt, HelpPrompt } from "../state/types";

function useFocusFirstButton(open: boolean) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (open) ref.current?.querySelector<HTMLButtonElement>("button")?.focus();
  }, [open]);
  return ref;
}

export function ConfirmDialog({ prompt, onAnswer }: { prompt: ConfirmPrompt | null; onAnswer: (approved: boolean) => void }) {
  const ref = useFocusFirstButton(Boolean(prompt));
  if (!prompt) return null;
  const { title, detail } = describeAction(prompt.action);
  return (
    <div className="overlay">
      <div className="dialog dialog-danger" role="alertdialog" aria-modal="true" aria-labelledby="confirm-title" ref={ref}>
        <h2 id="confirm-title">要讓 AI 執行這個動作嗎？</h2>
        <p className="dialog-action">
          <strong>{title}</strong>
          <span className="muted"> · {detail}</span>
        </p>
        <p className="dialog-reason">{reasonText(prompt.reason)}</p>
        {prompt.thumbnail && <img className="dialog-shot" src={`data:image/jpeg;base64,${prompt.thumbnail}`} alt="執行前的畫面" />}
        <p className="muted small">5 分鐘內沒有回應會視為拒絕。</p>
        <div className="dialog-buttons">
          <button className="btn" onClick={() => onAnswer(false)}>
            拒絕
          </button>
          <button className="btn btn-danger" onClick={() => onAnswer(true)}>
            允許執行
          </button>
        </div>
      </div>
    </div>
  );
}

export function HelpDialog({ prompt, onDone, onStop }: { prompt: HelpPrompt | null; onDone: () => void; onStop: () => void }) {
  const ref = useFocusFirstButton(Boolean(prompt));
  if (!prompt) return null;
  return (
    <div className="overlay">
      <div className="dialog" role="dialog" aria-modal="true" aria-labelledby="help-title" ref={ref}>
        <h2 id="help-title">AI 需要你幫忙</h2>
        <p className="dialog-reason">{prompt.reason}</p>
        <p className="muted small">請到 AI 開啟的瀏覽器視窗裡處理（例如登入、輸入驗證碼），完成後按「我處理好了」。</p>
        <div className="dialog-buttons">
          <button className="btn" onClick={onStop}>
            停止任務
          </button>
          <button className="btn btn-primary" onClick={onDone}>
            我處理好了
          </button>
        </div>
      </div>
    </div>
  );
}
