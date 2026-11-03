import type { Citation } from "../api/types";

export function CitationViewer({ citation, onClose }: { citation: Citation; onClose: () => void }) {
  const { document_id: id, page } = citation;
  return (
    <aside aria-label="Documento citado" className="flex h-full flex-col gap-2 rounded-md border border-slate-200 bg-white p-3">
      <div className="flex items-start justify-between gap-2">
        <h2 className="font-semibold">
          {citation.document_title} · pág. {page}
        </h2>
        <button type="button" className="btn-secondary" onClick={onClose}>
          Cerrar
        </button>
      </div>
      <blockquote className="border-l-4 border-slate-300 pl-3 text-sm text-slate-700">{citation.fragment}</blockquote>
      <iframe
        key={`${id}-${page}`}
        title={`${citation.document_title}, página ${page}`}
        src={`/api/documents/${id}/file#page=${page}`}
        className="min-h-[60vh] w-full flex-1 rounded border border-slate-200"
      />
    </aside>
  );
}
