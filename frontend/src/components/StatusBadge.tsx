import type { DocumentStatus } from "../api/types";
import { STATUS_LABELS, errorLabel } from "../labels";

const STYLES: Record<DocumentStatus, string> = {
  pending: "bg-slate-100 text-slate-700",
  processing: "bg-blue-100 text-blue-800",
  ready: "bg-green-100 text-green-800",
  failed: "bg-red-100 text-red-800",
};

export function StatusBadge({ status, errorCode }: { status: DocumentStatus; errorCode: string | null }) {
  return (
    <span aria-live="polite">
      <span className={`inline-block rounded-full px-2 py-0.5 text-xs font-medium ${STYLES[status]}`}>
        {STATUS_LABELS[status]}
      </span>
      {status === "failed" && <span className="mt-1 block text-xs text-red-700">{errorLabel(errorCode)}</span>}
    </span>
  );
}
