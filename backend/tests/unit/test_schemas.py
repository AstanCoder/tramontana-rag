from datetime import date

import pytest
from pydantic import ValidationError

from app.schemas import QuestionFilters, QuestionIn


def test_filters_reject_inverted_date_range() -> None:
    with pytest.raises(ValidationError):
        QuestionFilters(date_from=date(2024, 2, 1), date_to=date(2024, 1, 1))


@pytest.mark.parametrize("question", ["ab", "x" * 1001, "hola\x00mundo"])
def test_question_rejects_bad_length_or_control_chars(question: str) -> None:
    with pytest.raises(ValidationError):
        QuestionIn(question=question)
