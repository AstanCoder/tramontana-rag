import { useDocuments, useRetryDocument } from "../api/hooks";
import { DOC_TYPE_LABELS } from "../labels";
import { ErrorMessage } from "./ErrorMessage";
import { StatusBadge } from "./StatusBadge";

export function DocumentTable() {
  const { data, isPending, error } = useDocuments();
  const retry = useRetryDocument();

  if (isPending) return <p className="text-sm text-slate-600">Cargando documentos…</p>;
  if (error) return <ErrorMessage error={error} />;
  if (data.items.length === 0) return <p className="text-sm text-slate-600">Todavía no hay documentos.</p>;

  return (
    <div className="space-y-2">
      {retry.error && <ErrorMessage error={retry.error} />}
      <div className="overflow-x-auto rounded-md border border-slate-200 bg-white">
        <table className="w-full text-left text-sm">
          <thead className="bg-slate-50 text-xs uppercase text-slate-600">
            <tr>
              <th className="px-3 py-2">Título</th>
              <th className="px-3 py-2">Tipo</th>
              <th className="px-3 py-2">Póliza</th>
              <th className="px-3 py-2">Fecha</th>
              <th className="px-3 py-2">Estado</th>
              <th className="px-3 py-2">Vigente</th>
              <th className="px-3 py-2" />
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {data.items.map((d) => (
              <tr key={d.id}>
                <td className="px-3 py-2 font-medium">{d.title}</td>
                <td className="px-3 py-2">{DOC_TYPE_LABELS[d.doc_type]}</td>
                <td className="px-3 py-2">{d.policy_number ?? "—"}</td>
                <td className="px-3 py-2">{d.document_date ?? "—"}</td>
                <td className="px-3 py-2">
                  <StatusBadge status={d.status} errorCode={d.error_code} />
                </td>
                <td className="px-3 py-2">{d.is_current ? "Sí" : "No"}</td>
                <td className="px-3 py-2">
                  {d.status === "failed" && (
                    <button
                      type="button"
                      className="btn-secondary"
                      disabled={retry.isPending}
                      onClick={() => retry.mutate(d.id)}
                    >
                      Reintentar
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
