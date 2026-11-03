import uuid
from datetime import date

import pytest
from pydantic import ValidationError

from app.schemas import AnswerOut, QuestionFilters, QuestionIn


def test_filters_reject_inverted_date_range() -> None:
    with pytest.raises(ValidationError):
        QuestionFilters(date_from=date(2024, 2, 1), date_to=date(2024, 1, 1))


@pytest.mark.parametrize("question", ["ab", "x" * 1001, "hola\x00mundo"])
def test_question_rejects_bad_length_or_control_chars(question: str) -> None:
    with pytest.raises(ValidationError):
        QuestionIn(question=question)


def test_question_allows_newlines_and_tabs() -> None:
    assert QuestionIn(question="línea 1\n\tlínea 2").question == "línea 1\n\tlínea 2"


def test_answer_stored_before_page_end_still_loads() -> None:
    stored = {
        "status": "answered",
        "answer": "300 €",
        "citations": [
            {
                "document_id": str(uuid.uuid4()),
                "document_title": "Póliza hogar",
                "page": 2,
                "fragment": "deducible 300 €",
                "chunk_id": str(uuid.uuid4()),
            }
        ],
    }
    assert AnswerOut.model_validate(stored).citations[0].page_end is None
