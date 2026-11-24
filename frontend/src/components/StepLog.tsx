import { describeAction, shortModel } from "../lib/format";
import type { StepView } from "../state/types";

/** 這一步由哪一層處理：重播（不用 AI）、第 3 層（Decisions）、反射層（Decisions 照規則按鍵）、代理人模型。 */
function LayerLabel({ step }: { step: StepView }) {
  if (step.layer === "skill") return <span className="layer layer-skill">重播</span>;
  if (step.layer === "actor") return <span className="layer layer-actor">Decisions</span>;
  if (step.layer === "reflex") return <span className="layer layer-reflex">反射</span>;
  return <span className="mono">{shortModel(step.model)}</span>;
}

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
              {step.ok && step.layer === "skill" && step.message && <div className="step-detail">{step.message}</div>}
              {step.ok && step.action.kind === "set_reflex" && step.message && (
                <div className="step-detail">{step.message}</div>
              )}
            </div>
            <div className="step-meta">
              <LayerLabel step={step} />
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
