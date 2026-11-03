import unicodedata
import uuid
from datetime import date, datetime
from typing import Literal, Self

from pydantic import BaseModel, Field, field_validator, model_validator

from app.models import DocType, DocumentStatus, QuestionStatus

Confidence = Literal["high", "medium", "low"]


class DocumentOut(BaseModel):
    id: uuid.UUID
    title: str
    original_filename: str
    doc_type: DocType
    policy_number: str | None
    document_date: date | None
    status: DocumentStatus
    error_code: str | None
    page_count: int | None
    chunk_count: int
    is_current: bool
    created_at: datetime


class DocumentList(BaseModel):
    items: list[DocumentOut]


class QuestionFilters(BaseModel):
    doc_type: DocType | None = None
    policy_number: str | None = None
    date_from: date | None = None
    date_to: date | None = None
    include_superseded: bool = False

    @model_validator(mode="after")
    def _ordered_dates(self) -> Self:
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValueError("date_from must not be after date_to")
        return self


class QuestionIn(BaseModel):
    question: str = Field(min_length=3, max_length=1000)
    filters: QuestionFilters = Field(default_factory=QuestionFilters)

    @field_validator("question")
    @classmethod
    def _no_control_chars(cls, value: str) -> str:
        if any(unicodedata.category(c) == "Cc" and c not in "\n\t" for c in value):
            raise ValueError("control characters are not allowed")
        return value


class Citation(BaseModel):
    document_id: uuid.UUID
    document_title: str
    page: int
    fragment: str
    chunk_id: uuid.UUID


class AnswerOut(BaseModel):
    status: Literal["answered", "no_evidence"]
    answer: str | None = None
    citations: list[Citation] = Field(default_factory=list)
    confidence: Confidence | None = None
    degraded: bool = False
    reason: str | None = None


class QuestionOut(BaseModel):
    id: uuid.UUID
    status: QuestionStatus
    created_at: datetime
    result: AnswerOut | None = None
    error_code: str | None = None


class LLMCitation(BaseModel):
    chunk_id: str
    quote: str


class LLMAnswer(BaseModel):
    answer: str
    citations: list[LLMCitation]
    confidence: Confidence
    no_evidence: bool
