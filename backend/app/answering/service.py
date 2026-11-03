import time
import uuid
from datetime import timedelta

from opentelemetry import trace
from sqlalchemy import and_, func, or_, update
from sqlalchemy.orm import Session

from app.answering.citations import (
    NO_EVIDENCE_REASON,
    extractive_answer,
    validate_answer,
)
from app.answering.prompt import build_messages, new_nonce
from app.config import get_settings
from app.llm.gateway import (
    CompletionMeta,
    Gateway,
    LLMOutputInvalidError,
    ProviderUnavailableError,
)
from app.logging import content_free_span, current_trace_id, get_logger
from app.models import Question, QuestionStatus
from app.pii import redact
from app.retrieval.search import RetrievedChunk, has_evidence, search
from app.schemas import AnswerOut, LLMAnswer, QuestionFilters

log = get_logger(__name__)
_tracer = trace.get_tracer("app.answering")

# Must exceed the answer task's hard time limit (540 s) so a live task is never reclaimed.
STALE_PROCESSING = timedelta(minutes=10)


def answer(
    session: Session, gateway: Gateway, question: str, filters: QuestionFilters
) -> tuple[AnswerOut, list[RetrievedChunk], CompletionMeta | None]:
    # Embedding errors propagate: without retrieval there is nothing to fall back to.
    embedding = gateway.embed([redact(question)])[0]
    hits = search(session, question, embedding, filters)
    evidence = has_evidence(hits, get_settings().evidence_min_similarity)

    llm: LLMAnswer | None = None
    meta: CompletionMeta | None = None
    if evidence:
        try:
            llm, meta = gateway.complete_json(
                build_messages(question, hits, new_nonce()), LLMAnswer
            )
        except (ProviderUnavailableError, LLMOutputInvalidError) as exc:
            log.warning("answer.extractive_fallback", exc_type=type(exc).__name__)

    with content_free_span(_tracer, "answer.validate") as span:
        span.set_attribute("answer.citations_dropped", 0)
        if not evidence:
            out = AnswerOut(status="no_evidence", reason=NO_EVIDENCE_REASON)
        elif llm is None:
            out = extractive_answer(hits)
            span.set_attribute("llm.fallback_used", "extractive")
        else:
            out = validate_answer(llm, hits)
        span.set_attributes(
            {
                "answer.status": out.status,
                "answer.citations_valid": len(out.citations),
                "answer.degraded": out.degraded,
            }
        )
    return out, hits, meta


def run_question(session: Session, question_id: uuid.UUID, gateway: Gateway) -> None:
    # No owner token: questions have no retry endpoint. A stale `processing` row is a redelivery
    # after the worker died (acks_late), so it is reclaimed instead of staying stuck forever.
    question = session.scalars(
        update(Question)
        .where(
            Question.id == question_id,
            or_(
                Question.status == QuestionStatus.pending,
                and_(
                    Question.status == QuestionStatus.processing,
                    Question.created_at < func.now() - STALE_PROCESSING,
                ),
            ),
        )
        .values(status=QuestionStatus.processing)
        .returning(Question)
        .execution_options(populate_existing=True)
    ).one_or_none()
    if question is None:
        log.info("answer.duplicate_skipped", question_id=str(question_id))
        return
    session.commit()  # publish `processing` before the slow provider calls

    started = time.perf_counter()
    out, hits, meta = answer(
        session, gateway, question.question, QuestionFilters.model_validate(question.filters)
    )
    question.latency_ms = round((time.perf_counter() - started) * 1000)
    question.result = out.model_dump(mode="json")
    question.status = QuestionStatus(out.status)
    question.model = meta.model if meta else None
    question.provider = meta.provider if meta else None
    question.trace_id = current_trace_id() or question.trace_id
    session.commit()
    log.info(
        "answer.done",
        question_id=str(question_id),
        status=out.status,
        hits=len(hits),
        citations=len(out.citations),
        degraded=out.degraded,
        latency_ms=question.latency_ms,
    )
