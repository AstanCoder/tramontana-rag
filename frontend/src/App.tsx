import { useState } from "react";
import { useQuestion } from "./api/hooks";
import type { Citation } from "./api/types";
import { AnswerCard } from "./components/AnswerCard";
import { AskForm } from "./components/AskForm";
import { CitationViewer } from "./components/CitationViewer";
import { DocumentTable } from "./components/DocumentTable";
import { ErrorMessage } from "./components/ErrorMessage";
import { UploadForm } from "./components/UploadForm";

type Tab = "documents" | "assistant";
const TABS: { id: Tab; label: string }[] = [
  { id: "documents", label: "Documentos" },
  { id: "assistant", label: "Asistente" },
];

export default function App() {
  const [tab, setTab] = useState<Tab>("documents");
  // Session-local history (newest first): each question is answered on its own, not as a dialogue.
  const [asked, setAsked] = useState<{ id: string; text: string }[]>([]);
  const [citation, setCitation] = useState<Citation | null>(null);

  return (
    <div className="mx-auto max-w-6xl space-y-4 p-4">
      <h1 className="text-xl font-semibold">Tramontana · Asistente de documentos</h1>
      <div role="tablist" className="flex gap-1 border-b border-slate-200">
        {TABS.map((t) => (
          <button
            key={t.id}
            type="button"
            role="tab"
            id={`tab-${t.id}`}
            aria-selected={tab === t.id}
            aria-controls={`panel-${t.id}`}
            className={`tab ${tab === t.id ? "border-slate-900 text-slate-900" : "border-transparent text-slate-600"}`}
            onClick={() => setTab(t.id)}
          >
            {t.label}
          </button>
        ))}
      </div>

      {/* Panels stay mounted (hidden) so the question and its answer survive tab switches. */}
      <div role="tabpanel" id="panel-documents" aria-labelledby="tab-documents" hidden={tab !== "documents"} className="space-y-4">
        <UploadForm />
        <DocumentTable />
      </div>

      <div role="tabpanel" id="panel-assistant" aria-labelledby="tab-assistant" hidden={tab !== "assistant"}>
        <div className={citation ? "grid gap-4 lg:grid-cols-2" : ""}>
          <div className="space-y-4">
            <AskForm
              onAsked={(id, text) => {
                setAsked((prev) => [{ id, text }, ...prev]);
                setCitation(null);
              }}
            />
            {asked.map((q) => (
              <AskedQuestion key={q.id} id={q.id} text={q.text} onSelectCitation={setCitation} />
            ))}
          </div>
          {citation && <CitationViewer citation={citation} onClose={() => setCitation(null)} />}
        </div>
      </div>
    </div>
  );
}

function AskedQuestion({
  id,
  text,
  onSelectCitation,
}: {
  id: string;
  text: string;
  onSelectCitation: (citation: Citation) => void;
}) {
  const question = useQuestion(id);
  return (
    <article className="space-y-2">
      <p className="whitespace-pre-wrap rounded-md bg-slate-100 p-3 text-sm font-medium">{text}</p>
      {question.error ? (
        <ErrorMessage error={question.error} />
      ) : (
        <AnswerCard question={question.data} onSelectCitation={onSelectCitation} />
      )}
    </article>
  );
}
