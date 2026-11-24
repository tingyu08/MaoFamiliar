import { describeAction, shortModel } from "../lib/format";
import type { StepView } from "../state/types";

export function StepLog({ steps, screenshotUrl }: { steps: StepView[]; screenshotUrl?: (index: number) => string }) {
  if (steps.length === 0) return <p className="muted empty">還沒有步驟</p>;
  return (
    <ol className="steps" aria-label="步驟紀錄">
      {[...steps].reverse().map((step) => {
        const { title, detail } = describeAction(step.action);
        return (
          <li key={step.index} className={`step ${step.ok ? "step-ok" : "step-fail"}`}>
            <span className="step-index">{step.index + 1}</span>
            <div className="step-body">
              <div className="step-title">
                {title}
                {step.action.risk === "high" && <span className="tag tag-risk">高風險</span>}
              </div>
              <div className="step-detail">{detail}</div>
              {!step.ok && step.message && <div className="step-error">{step.message}</div>}
            </div>
            <div className="step-meta">
              <span className="mono">{shortModel(step.model)}</span>
              {screenshotUrl && (
                <a href={screenshotUrl(step.index)} target="_blank" rel="noreferrer">
                  截圖
                </a>
              )}
            </div>
          </li>
        );
      })}
    </ol>
  );
}
