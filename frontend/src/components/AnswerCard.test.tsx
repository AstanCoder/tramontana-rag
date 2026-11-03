import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { Citation, QuestionOut } from "../api/types";
import { AnswerCard } from "./AnswerCard";

const citation: Citation = {
  document_id: "d1",
  document_title: "Póliza Hogar",
  page: 2,
  page_end: 2,
  fragment: "La franquicia es de 300 euros.",
  chunk_id: "c1",
};
const question = (result: QuestionOut["result"], status: QuestionOut["status"] = "answered"): QuestionOut => ({
  id: "q1",
  status,
  created_at: "2026-01-01T00:00:00Z",
  result,
  error_code: null,
  trace_id: null,
});

describe("AnswerCard", () => {
  it("renders citations and reports the selected one", async () => {
    const onSelect = vi.fn();
    render(
      <AnswerCard
        question={question({ status: "answered", answer: "300 euros.", citations: [citation], confidence: "high", degraded: false, reason: null })}
        onSelectCitation={onSelect}
      />,
    );
    expect(screen.getByText("300 euros.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /pág\. 2/ }));
    expect(onSelect).toHaveBeenCalledWith(citation);
  });

  it("renders the no-evidence card with its reason and no citations", () => {
    render(
      <AnswerCard
        question={question({ status: "no_evidence", answer: null, citations: [], confidence: null, degraded: false, reason: "Nada relevante" }, "no_evidence")}
        onSelectCitation={() => {}}
      />,
    );
    expect(screen.getByText("Sin evidencia suficiente")).toBeInTheDocument();
    expect(screen.getByText("Nada relevante")).toBeInTheDocument();
    expect(screen.queryByRole("list")).not.toBeInTheDocument();
  });

  it("renders the degraded banner", () => {
    render(
      <AnswerCard
        question={question({ status: "answered", answer: "Fragmento", citations: [citation], confidence: "low", degraded: true, reason: null })}
        onSelectCitation={() => {}}
      />,
    );
    expect(screen.getByText(/modelo no está disponible/)).toBeInTheDocument();
  });

  it("renders a citation spanning pages as a page range", () => {
    const spanning = { ...citation, page_end: 3 };
    render(
      <AnswerCard
        question={question({ status: "answered", answer: "300 euros.", citations: [spanning], confidence: "high", degraded: false, reason: null })}
        onSelectCitation={() => {}}
      />,
    );
    expect(screen.getByRole("button", { name: /págs\. 2–3/ })).toBeInTheDocument();
  });

  it("renders a failed question with its label and trace id", () => {
    render(
      <AnswerCard
        question={{ ...question(null, "failed"), error_code: "answer_timeout", trace_id: "abc123" }}
        onSelectCitation={() => {}}
      />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent(/tiempo/i);
    expect(screen.getByRole("alert")).toHaveTextContent("trace_id: abc123");
  });

  it("renders the loading state", () => {
    render(<AnswerCard question={question(null, "processing")} onSelectCitation={() => {}} />);
    expect(screen.getByText("Buscando en los documentos…")).toBeInTheDocument();
  });
});
