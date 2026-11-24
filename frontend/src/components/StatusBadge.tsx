import { STATUS_LABEL } from "../lib/format";
import type { TaskStatus } from "../state/types";

export function StatusBadge({ status }: { status: TaskStatus | null }) {
  if (!status) return <span className="badge badge-idle">待命</span>;
  return <span className={`badge badge-${status}`}>{STATUS_LABEL[status]}</span>;
}
