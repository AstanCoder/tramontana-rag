import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.answering.tasks import answer_question
from app.db import get_session
from app.errors import AppError
from app.guards import validate_question
from app.logging import current_trace_id, get_logger
from app.models import Question, QuestionStatus
from app.schemas import QuestionIn, QuestionOut

log = get_logger(__name__)

router = APIRouter(prefix="/api/questions", tags=["questions"])

SessionDep = Annotated[Session, Depends(get_session)]


@router.post("", status_code=202)
def create_question(body: QuestionIn, session: SessionDep) -> QuestionOut:
    question = Question(
        question=validate_question(body.question),
        filters=body.filters.model_dump(mode="json"),
        trace_id=current_trace_id(),
    )
    session.add(question)
    session.commit()
    try:
        answer_question.apply_async(args=(str(question.id),))
    except Exception as exc:
        # Without this the row would stay `pending` forever.
        log.error("enqueue_failed", question_id=str(question.id), exc_type=type(exc).__name__)
        question.status = QuestionStatus.failed
        question.error_code = "enqueue_failed"
        session.commit()
        raise AppError(
            "queue_unavailable",
            "La cola de procesamiento no está disponible. Inténtalo de nuevo más tarde.",
            503,
        ) from exc
    session.refresh(question)  # load server defaults (created_at)
    return QuestionOut.model_validate(question, from_attributes=True)


@router.get("/{question_id}")
def get_question(question_id: uuid.UUID, session: SessionDep) -> QuestionOut:
    question = session.get(Question, question_id)
    if question is None:
        raise AppError("question_not_found", "Pregunta no encontrada", 404)
    return QuestionOut.model_validate(question, from_attributes=True)
