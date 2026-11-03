import { ApiError } from "../api/client";
import { errorLabel } from "../labels";

export function ErrorMessage({ error }: { error: unknown }) {
  const api = error instanceof ApiError ? error : null;
  return (
    <div role="alert" className="rounded-md border border-red-300 bg-red-50 p-3 text-sm text-red-900">
      <p>{api ? errorLabel(api.code, api.message) : "No se pudo conectar con el servidor."}</p>
      {api?.traceId && <p className="mt-1 text-xs text-red-700">trace_id: {api.traceId}</p>}
    </div>
  );
}
