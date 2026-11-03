import random
import uuid

from celery import Task
from celery.exceptions import SoftTimeLimitExceeded
from sqlalchemy import update
from sqlalchemy.exc import OperationalError

from app.db import session_scope
from app.llm.gateway import ProviderUnavailableError, get_gateway
from app.logging import get_logger
from app.models import Document, DocumentStatus
from app.worker import celery_app

log = get_logger(__name__)

RETRY_BACKOFF_S = (30, 60, 120)
RETRY_JITTER_S = 5.0


def _mark_failed(document_id: uuid.UUID, task_id: str, error_code: str) -> None:
    with session_scope() as session:
        # Owner token + processing: never fail a successor's run or an already committed `ready`.
        session.execute(
            update(Document)
            .where(
                Document.id == document_id,
                Document.ingest_task_id == task_id,
                Document.status == DocumentStatus.processing,
            )
            .values(status=DocumentStatus.failed, error_code=error_code)
        )
    log.warning("ingest.failed", document_id=str(document_id), error_code=error_code)


@celery_app.task(bind=True, max_retries=len(RETRY_BACKOFF_S), soft_time_limit=900, time_limit=960)
def ingest_document(self: "Task[[str], None]", document_id: str) -> None:
    # Imported here so the API process, which only enqueues, never loads Docling/torch.
    from app.ingestion.parsing import CorruptDocumentError, UnreadableDocumentError
    from app.ingestion.service import run_ingestion

    doc_id = uuid.UUID(document_id)
    task_id = self.request.id or ""  # never None in a worker; "" would match no owner token
    retries = self.request.retries
    try:
        with session_scope() as session:
            run_ingestion(session, doc_id, task_id, get_gateway())
    except CorruptDocumentError:
        _mark_failed(doc_id, task_id, "corrupt_document")
    except UnreadableDocumentError:
        _mark_failed(doc_id, task_id, "unreadable_document")
    except SoftTimeLimitExceeded:
        _mark_failed(doc_id, task_id, "ingestion_timeout")
    except (ProviderUnavailableError, OperationalError) as exc:
        if retries >= len(RETRY_BACKOFF_S):
            _mark_failed(doc_id, task_id, "provider_unavailable")
            return
        retry_after = getattr(exc, "retry_after", None) or 0
        jitter = random.uniform(0, RETRY_JITTER_S)  # noqa: S311 - not security-sensitive
        countdown = max(retry_after, RETRY_BACKOFF_S[retries]) + jitter
        log.warning(
            "ingest.retry",
            document_id=document_id,
            attempt=retries + 1,
            countdown_s=round(countdown),
            exc_type=type(exc).__name__,
        )
        raise self.retry(countdown=countdown) from exc
    except Exception as exc:
        log.error("ingest.error", document_id=document_id, exc_type=type(exc).__name__)
        _mark_failed(doc_id, task_id, "ingestion_error")
