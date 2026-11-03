import type { Citation, Confidence, QuestionOut } from "../api/types";
import { errorLabel, pageLabel } from "../labels";

const CONFIDENCE: Record<Confidence, string> = {
  high: "Confianza alta",
  medium: "Confianza media",
  low: "Confianza baja",
};

interface Props {
  question: QuestionOut | undefined;
  onSelectCitation: (citation: Citation) => void;
}

export function AnswerCard({ question, onSelectCitation }: Props) {
  return (
    <section aria-live="polite" className="space-y-3">
      <Body question={question} onSelectCitation={onSelectCitation} />
    </section>
  );
}

function Body({ question, onSelectCitation }: Props) {
  if (!question || question.status === "pending" || question.status === "processing")
    return <p className="text-sm text-slate-600">Buscando en los documentos…</p>;

  if (question.status === "failed")
    return (
      <p role="alert" className="rounded-md border border-red-300 bg-red-50 p-3 text-sm text-red-900">
        {errorLabel(question.error_code, "No se pudo generar la respuesta.")}
        {question.trace_id && <span className="mt-1 block text-xs text-red-700">trace_id: {question.trace_id}</span>}
      </p>
    );

  const result = question.result;
  if (!result) return null;

  if (result.status === "no_evidence")
    return (
      <div className="rounded-md border border-amber-400 bg-amber-50 p-4 text-amber-950">
        <h3 className="font-semibold">Sin evidencia suficiente</h3>
        {result.reason && <p className="mt-1 text-sm">{result.reason}</p>}
      </div>
    );

  return (
    <div className="space-y-3 rounded-md border border-slate-200 bg-white p-4">
      {result.degraded && (
        <p className="rounded-md bg-amber-100 p-2 text-sm text-amber-900">
          El modelo no está disponible en este momento: se muestran únicamente los fragmentos más relevantes de los
          documentos.
        </p>
      )}
      <p className="whitespace-pre-wrap">{result.answer}</p>
      {result.confidence && (
        <span className="inline-block rounded-full bg-slate-100 px-2 py-0.5 text-xs font-medium text-slate-700">
          {CONFIDENCE[result.confidence]}
        </span>
      )}
      {result.citations.length > 0 && (
        <div>
          <h3 className="text-sm font-semibold">Citas</h3>
          <ul className="mt-1 space-y-1">
            {result.citations.map((c) => (
              <li key={c.chunk_id}>
                <button type="button" className="btn-citation" onClick={() => onSelectCitation(c)}>
                  <span className="font-medium">
                    {c.document_title} · {pageLabel(c)}
                  </span>
                  <span className="line-clamp-2 block text-slate-600">{c.fragment}</span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
