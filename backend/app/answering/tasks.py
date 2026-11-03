import uuid

from celery.exceptions import SoftTimeLimitExceeded
from sqlalchemy import update

from app.answering.service import run_question
from app.db import session_scope
from app.llm.gateway import ProviderUnavailableError, get_gateway
from app.logging import get_logger
from app.models import Question, QuestionStatus
from app.worker import celery_app

log = get_logger(__name__)


def _mark_failed(question_id: uuid.UUID, error_code: str) -> None:
    with session_scope() as session:
        # `pending` too: a crash before the claim must not leave the question pending forever.
        session.execute(
            update(Question)
            .where(
                Question.id == question_id,
                Question.status.in_([QuestionStatus.pending, QuestionStatus.processing]),
            )
            .values(status=QuestionStatus.failed, error_code=error_code)
        )
    log.warning("answer.failed", question_id=str(question_id), error_code=error_code)


@celery_app.task(soft_time_limit=480, time_limit=540)
def answer_question(question_id: str) -> None:
    qid = uuid.UUID(question_id)
    try:
        with session_scope() as session:
            run_question(session, qid, get_gateway())
    except SoftTimeLimitExceeded:
        _mark_failed(qid, "answer_timeout")
    except ProviderUnavailableError:
        # Only embedding errors reach here; completion errors fall back to extractive answers.
        _mark_failed(qid, "provider_unavailable")
    except Exception as exc:
        log.error("answer.error", question_id=question_id, exc_type=type(exc).__name__)
        _mark_failed(qid, "answer_error")
