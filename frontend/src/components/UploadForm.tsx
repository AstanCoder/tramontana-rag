import { useState, type FormEvent } from "react";
import { useUploadDocument } from "../api/hooks";
import { DOC_TYPE_LABELS } from "../labels";
import type { DocType } from "../api/types";
import { ErrorMessage } from "./ErrorMessage";

// Mirrors the backend's MAX_UPLOAD_MB default (and nginx client_max_body_size): change them together.
const MAX_BYTES = 25 * 1024 * 1024;

export function UploadForm() {
  const upload = useUploadDocument();
  const [clientError, setClientError] = useState<string | null>(null);

  function onSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const form = e.currentTarget;
    const data = new FormData(form);
    const file = data.get("file") as File;
    setClientError(null);
    upload.reset();
    if (!file.name) return setClientError("Selecciona un archivo PDF.");
    if (!file.name.toLowerCase().endsWith(".pdf") || (file.type && file.type !== "application/pdf"))
      return setClientError("Solo se admiten archivos PDF.");
    if (file.size > MAX_BYTES) return setClientError("El archivo supera el tamaño máximo de 25 MB.");
    for (const key of ["policy_number", "document_date"]) if (!data.get(key)) data.delete(key);
    upload.mutate(data, { onSuccess: () => form.reset() });
  }

  return (
    <form onSubmit={onSubmit} className="grid gap-3 rounded-md border border-slate-200 bg-white p-4 sm:grid-cols-2">
      <label className="field sm:col-span-2">
        Archivo PDF
        <input name="file" type="file" accept="application/pdf" className="input" />
      </label>
      <label className="field">
        Tipo de documento
        <select name="doc_type" className="input" defaultValue="policy">
          {(Object.keys(DOC_TYPE_LABELS) as DocType[]).map((t) => (
            <option key={t} value={t}>
              {DOC_TYPE_LABELS[t]}
            </option>
          ))}
        </select>
      </label>
      <label className="field">
        Número de póliza (opcional)
        <input name="policy_number" type="text" maxLength={64} className="input" />
      </label>
      <label className="field">
        Fecha del documento (opcional)
        <input name="document_date" type="date" className="input" />
      </label>
      <div className="flex items-end">
        <button type="submit" className="btn-primary" disabled={upload.isPending}>
          {upload.isPending ? "Subiendo…" : "Subir documento"}
        </button>
      </div>
      <div className="sm:col-span-2" aria-live="polite">
        {clientError && (
          <p role="alert" className="text-sm text-red-700">
            {clientError}
          </p>
        )}
        {upload.error && <ErrorMessage error={upload.error} />}
        {upload.isSuccess && <p className="text-sm text-green-700">Documento recibido. Se está procesando.</p>}
      </div>
    </form>
  );
}
