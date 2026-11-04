import time
import uuid
from pathlib import Path

from opentelemetry import trace
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from app.config import get_settings
from app.guards import looks_like_injection, sanitize_untrusted
from app.ingestion.chunking import ChunkDraft, chunk_document
from app.ingestion.parsing import parse_pdf
from app.llm.gateway import Gateway
from app.logging import content_free_span, get_logger
from app.models import Chunk, ChunkKind, DocType, Document, DocumentStatus
from app.pii import extract_policy_number, redact

log = get_logger(__name__)
_tracer = trace.get_tracer("app.ingestion")

# First run + 3 provider retries + 1 redelivery after a worker crash. Past it, a redelivered
# task is a PDF that kills the worker (e.g. Docling OOM) and would loop forever under acks_late.
MAX_INGEST_ATTEMPTS = 5


def claim_document(session: Session, document_id: uuid.UUID, task_id: str) -> Document | None:
    """Atomically take ownership; only the task whose id was stored at enqueue time wins."""
    stmt = (
        update(Document)
        .where(
            Document.id == document_id,
            Document.ingest_task_id == task_id,
            Document.status.in_([DocumentStatus.pending, DocumentStatus.processing]),
        )
        .values(
            status=DocumentStatus.processing,
            processing_started_at=func.now(),
            attempts=Document.attempts + 1,
        )
        .returning(Document)
        .execution_options(populate_existing=True)
    )
    return session.scalars(stmt).one_or_none()


def replace_chunks(
    session: Session,
    document: Document,
    drafts: list[ChunkDraft],
    embeddings: list[list[float]],
) -> None:
    session.execute(delete(Chunk).where(Chunk.document_id == document.id))
    session.add_all(
        Chunk(
            document_id=document.id,
            ordinal=draft.ordinal,
            kind=draft.kind,
            text=draft.text,
            text_redacted=redact(sanitize_untrusted(draft.text)),
            headings=draft.headings,
            page_start=draft.page_start,
            page_end=draft.page_end,
            embedding=embedding,
            suspicious=looks_like_injection(draft.text),
        )
        for draft, embedding in zip(drafts, embeddings, strict=True)
    )


def apply_versioning(session: Session, document: Document) -> None:
    """Among ready policies sharing a number, the latest document_date (then upload) is current."""
    if document.doc_type != DocType.policy or document.policy_number is None:
        return
    # Serialize concurrent ingests of the same policy so both cannot claim to be current.
    session.execute(select(func.pg_advisory_xact_lock(func.hashtext(document.policy_number))))
    versions = session.scalars(
        select(Document)
        .where(
            Document.doc_type == DocType.policy,
            Document.policy_number == document.policy_number,
            Document.status == DocumentStatus.ready,
        )
        .order_by(Document.document_date.desc().nulls_last(), Document.created_at.desc())
    ).all()
    current = versions[0]
    current.is_current = True
    current.superseded_by_id = None
    for older in versions[1:]:
        older.is_current = False
        older.superseded_by_id = current.id


def run_ingestion(session: Session, document_id: uuid.UUID, task_id: str, gateway: Gateway) -> None:
    document = claim_document(session, document_id, task_id)
    if document is None:
        log.info("ingest.duplicate_skipped", document_id=str(document_id))
        return
    if document.attempts > MAX_INGEST_ATTEMPTS:
        document.status = DocumentStatus.failed
        document.error_code = "ingestion_error"
        session.commit()
        log.warning("ingest.attempts_exhausted", document_id=str(document_id))
        return
    session.commit()  # publish `processing` before the long parse/embed steps
    started = time.perf_counter()

    with content_free_span(_tracer, "ingest.parse") as span:
        doc = parse_pdf(Path(document.storage_path), get_settings().max_pages)
        span.set_attribute("pages", len(doc.pages))
    with content_free_span(_tracer, "ingest.chunk") as span:
        drafts = chunk_document(doc)
        table_count = sum(d.kind == ChunkKind.table for d in drafts)
        span.set_attributes({"chunk_count": len(drafts), "table_count": table_count})
    with content_free_span(_tracer, "ingest.embed") as span:
        span.set_attribute("chunk_count", len(drafts))
        embeddings = gateway.embed([redact(sanitize_untrusted(d.embed_text)) for d in drafts])

    # Re-check ownership under a row lock: /retry may have handed the document to a new task
    # meanwhile, and the lock serializes any other writer of this document's chunks.
    document = session.scalars(
        select(Document)
        .where(
            Document.id == document_id,
            Document.ingest_task_id == task_id,
            Document.status == DocumentStatus.processing,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one_or_none()
    if document is None:
        session.rollback()
        log.info("ingest.superseded", document_id=str(document_id))
        return
    replace_chunks(session, document, drafts, embeddings)
    document.page_count = len(doc.pages)
    document.embedding_model = gateway.embedding_model
    if document.policy_number is None:
        document.policy_number = extract_policy_number("\n".join(d.embed_text for d in drafts))
    document.status = DocumentStatus.ready
    document.error_code = None
    session.flush()
    apply_versioning(session, document)
    session.commit()
    log.info(
        "ingest.ready",
        document_id=str(document_id),
        pages=document.page_count,
        chunk_count=len(drafts),
        table_count=table_count,
        duration_ms=round((time.perf_counter() - started) * 1000),
    )
