import uuid
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import Row, Select, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.documents.storage import StoredFile
from app.errors import AppError
from app.ingestion.tasks import ingest_document
from app.logging import get_logger
from app.models import Chunk, DocType, Document, DocumentStatus
from app.schemas import DocumentOut

log = get_logger(__name__)

# Must exceed the ingest task's hard time limit (960 s) so a live task is never replaced.
STALE_PROCESSING = timedelta(minutes=20)

_chunk_count = (
    select(func.count(Chunk.id))
    .where(Chunk.document_id == Document.id)
    .correlate(Document)
    .scalar_subquery()
)


def _out_query() -> Select[tuple[Document, int]]:
    return select(Document, _chunk_count)


def _to_out(row: Row[tuple[Document, int]]) -> DocumentOut:
    document, chunk_count = row
    fields = DocumentOut.model_fields.keys() - {"chunk_count"}
    return DocumentOut(**{f: getattr(document, f) for f in fields}, chunk_count=chunk_count)


def get_document_out(session: Session, document_id: uuid.UUID) -> DocumentOut:
    row = session.execute(_out_query().where(Document.id == document_id)).one_or_none()
    if row is None:
        raise AppError("document_not_found", "Documento no encontrado", 404)
    return _to_out(row)


def list_documents(
    session: Session,
    doc_type: DocType | None,
    policy_number: str | None,
    status: DocumentStatus | None,
) -> list[DocumentOut]:
    # shortcut: newest 200 only, no pagination; add cursor paging when the UI needs older documents.
    query = _out_query().order_by(Document.created_at.desc()).limit(200)
    if doc_type:
        query = query.where(Document.doc_type == doc_type)
    if policy_number:
        query = query.where(Document.policy_number == policy_number)
    if status:
        query = query.where(Document.status == status)
    return [_to_out(row) for row in session.execute(query)]


def get_document(session: Session, document_id: uuid.UUID) -> Document:
    document = session.get(Document, document_id)
    if document is None:
        raise AppError("document_not_found", "Documento no encontrado", 404)
    return document


def create_or_get_document(
    session: Session,
    stored: StoredFile,
    doc_type: DocType,
    policy_number: str | None,
    document_date: date | None,
    title: str,
) -> tuple[Document, bool]:
    """Insert relying on the unique sha256; a duplicate (or lost race) returns the existing row."""
    document = Document(
        title=title,
        original_filename=stored.original_filename,
        sha256=stored.sha256,
        doc_type=doc_type,
        policy_number=policy_number,
        document_date=document_date,
        storage_path=str(stored.path),
    )
    session.add(document)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        stored.path.unlink(missing_ok=True)
        existing = session.scalars(select(Document).where(Document.sha256 == stored.sha256)).one()
        return existing, False
    return document, True


def enqueue_ingestion(session: Session, document: Document) -> None:
    # The task id is persisted first: it is the owner token the worker claims the document with.
    task_id = str(uuid.uuid4())
    document.ingest_task_id = task_id
    session.commit()
    try:
        ingest_document.apply_async(args=(str(document.id),), task_id=task_id)
    except Exception as exc:
        # Fail now so the row is retryable at once (a `pending` row waits out the 20 min lease).
        log.error("enqueue_failed", document_id=str(document.id), exc_type=type(exc).__name__)
        document.status = DocumentStatus.failed
        document.error_code = "enqueue_failed"
        session.commit()
        raise AppError(
            "queue_unavailable",
            "La cola de procesamiento no está disponible. Inténtalo de nuevo más tarde.",
            503,
        ) from exc


def retry_document(session: Session, document_id: uuid.UUID) -> Document:
    document = get_document(session, document_id)
    lease_expired = datetime.now(UTC) - STALE_PROCESSING
    # A `pending` row this old lost its broker message (e.g. Redis restarted without persistence).
    stale = (
        document.status == DocumentStatus.processing
        and document.processing_started_at is not None
        and document.processing_started_at < lease_expired
    ) or (document.status == DocumentStatus.pending and document.updated_at < lease_expired)
    if document.status != DocumentStatus.failed and not stale:
        raise AppError("invalid_state", "El documento no se puede reintentar ahora", 409)
    document.status = DocumentStatus.pending
    document.error_code = None
    document.attempts = 0  # a manual retry gets a fresh attempt budget
    enqueue_ingestion(session, document)
    return document
