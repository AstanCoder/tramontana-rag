import unicodedata

from opentelemetry import trace

from app.guards import is_safe_answer_text
from app.retrieval.search import RetrievedChunk
from app.schemas import AnswerOut, Citation, LLMAnswer

MIN_QUOTE_LEN = 8
EXTRACTIVE_FRAGMENT_LEN = 300
NO_EVIDENCE_REASON = "No hay evidencia suficiente en los documentos para responder."
UNSAFE_ANSWER_REASON = "La respuesta generada no superó la validación y se ha descartado."
EXTRACTIVE_ANSWER = (
    "No se pudo generar una respuesta en este momento; estos son los fragmentos más "
    "relevantes de la documentación."
)


def normalize_for_match(s: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", s).casefold().split())


def _citation(hit: RetrievedChunk, fragment: str) -> Citation:
    # Document and page come from the retrieved chunk, never from the model.
    return Citation(
        document_id=hit.document_id,
        document_title=hit.document_title,
        page=hit.page_start,
        page_end=hit.page_end,
        fragment=fragment,
        chunk_id=hit.chunk_id,
    )


def validate_answer(llm: LLMAnswer, hits: list[RetrievedChunk]) -> AnswerOut:
    by_id = {str(h.chunk_id): h for h in hits}
    citations: dict[str, Citation] = {}
    dropped = 0
    for c in llm.citations:
        hit = by_id.get(c.chunk_id)
        quote = c.quote.strip()
        if (
            hit is None
            or len(quote) < MIN_QUOTE_LEN
            or normalize_for_match(quote) not in normalize_for_match(hit.text_redacted)
        ):
            dropped += 1
        elif c.chunk_id not in citations:
            citations[c.chunk_id] = _citation(hit, quote)
    trace.get_current_span().set_attribute("answer.citations_dropped", dropped)

    if llm.no_evidence or not citations:
        return AnswerOut(status="no_evidence", reason=NO_EVIDENCE_REASON)
    if not llm.answer.strip() or not is_safe_answer_text(llm.answer):
        return AnswerOut(status="no_evidence", reason=UNSAFE_ANSWER_REASON)
    return AnswerOut(
        status="answered",
        answer=llm.answer,
        citations=list(citations.values()),
        confidence="low" if dropped else llm.confidence,
    )


def extractive_answer(hits: list[RetrievedChunk], max_citations: int = 3) -> AnswerOut:
    """Last-resort answer when the LLM is unavailable: the best chunks, valid by construction."""
    return AnswerOut(
        status="answered",
        answer=EXTRACTIVE_ANSWER,
        citations=[
            _citation(h, h.text_redacted[:EXTRACTIVE_FRAGMENT_LEN]) for h in hits[:max_citations]
        ],
        confidence="low",
        degraded=True,
    )
