import type { FormEvent } from "react";
import { useAskQuestion } from "../api/hooks";
import type { DocType, QuestionFilters } from "../api/types";
import { DOC_TYPE_LABELS } from "../labels";
import { ErrorMessage } from "./ErrorMessage";

export function AskForm({ onAsked }: { onAsked: (questionId: string, question: string) => void }) {
  const ask = useAskQuestion();

  function onSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const data = new FormData(e.currentTarget);
    const text = (key: string) => (data.get(key) as string).trim();
    const filters: QuestionFilters = { include_superseded: data.get("include_superseded") === "on" };
    if (text("doc_type")) filters.doc_type = text("doc_type") as DocType;
    for (const key of ["policy_number", "date_from", "date_to"] as const)
      if (text(key)) filters[key] = text(key);
    const question = text("question");
    ask.mutate({ question, filters }, { onSuccess: (q) => onAsked(q.id, question) });
  }

  return (
    <form onSubmit={onSubmit} className="grid gap-3 rounded-md border border-slate-200 bg-white p-4 sm:grid-cols-2">
      <label className="field sm:col-span-2">
        Pregunta
        <textarea name="question" required minLength={3} maxLength={1000} rows={3} className="input" />
      </label>
      <label className="field">
        Tipo de documento
        <select name="doc_type" className="input" defaultValue="">
          <option value="">Todos</option>
          {(Object.keys(DOC_TYPE_LABELS) as DocType[]).map((t) => (
            <option key={t} value={t}>
              {DOC_TYPE_LABELS[t]}
            </option>
          ))}
        </select>
      </label>
      <label className="field">
        Número de póliza
        <input name="policy_number" type="text" maxLength={64} className="input" />
      </label>
      <label className="field">
        Desde
        <input name="date_from" type="date" className="input" />
      </label>
      <label className="field">
        Hasta
        <input name="date_to" type="date" className="input" />
      </label>
      <label className="flex items-center gap-2 text-sm sm:col-span-2">
        <input name="include_superseded" type="checkbox" className="size-4" />
        Incluir versiones anteriores
      </label>
      <div className="sm:col-span-2">
        <button type="submit" className="btn-primary" disabled={ask.isPending}>
          {ask.isPending ? "Enviando…" : "Preguntar"}
        </button>
      </div>
      {ask.error && (
        <div className="sm:col-span-2">
          <ErrorMessage error={ask.error} />
        </div>
      )}
    </form>
  );
}
