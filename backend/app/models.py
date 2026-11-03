import uuid
from datetime import date, datetime
from enum import StrEnum
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    ARRAY,
    Boolean,
    Computed,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy import text as sql_text
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

EMBEDDING_DIM = 768


class DocType(StrEnum):
    policy = "policy"
    general_conditions = "general_conditions"
    claim_letter = "claim_letter"
    other = "other"


class DocumentStatus(StrEnum):
    pending = "pending"
    processing = "processing"
    failed = "failed"
    ready = "ready"


class QuestionStatus(StrEnum):
    pending = "pending"
    processing = "processing"
    answered = "answered"
    no_evidence = "no_evidence"
    failed = "failed"


class ChunkKind(StrEnum):
    text = "text"
    table = "table"


def _enum(enum_cls: type[StrEnum]) -> Enum:
    # Stored as plain VARCHAR holding the lowercase value (no native PG enum to migrate).
    return Enum(
        enum_cls,
        native_enum=False,
        length=32,
        values_callable=lambda e: [m.value for m in e],
    )


class Base(DeclarativeBase):
    pass


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title: Mapped[str] = mapped_column(String(255))
    original_filename: Mapped[str] = mapped_column(String(255))
    sha256: Mapped[str] = mapped_column(String(64), unique=True)
    doc_type: Mapped[DocType] = mapped_column(_enum(DocType))
    policy_number: Mapped[str | None] = mapped_column(String(64), index=True)
    document_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[DocumentStatus] = mapped_column(
        _enum(DocumentStatus), default=DocumentStatus.pending, server_default="pending"
    )
    error_code: Mapped[str | None] = mapped_column(String(64))
    page_count: Mapped[int | None] = mapped_column(Integer)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True, server_default=sql_text("true"))
    superseded_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("documents.id"))
    storage_path: Mapped[str] = mapped_column(String(512))
    ingest_task_id: Mapped[str | None] = mapped_column(String(64))
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    processing_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    chunks: Mapped[list["Chunk"]] = relationship(
        back_populates="document", cascade="all, delete-orphan", passive_deletes=True
    )

    __table_args__ = (
        Index(
            "ix_documents_policy_number_current",
            "policy_number",
            postgresql_where=sql_text("is_current"),
        ),
    )


class Chunk(Base):
    __tablename__ = "chunks"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    ordinal: Mapped[int] = mapped_column(Integer)
    kind: Mapped[ChunkKind] = mapped_column(_enum(ChunkKind))
    text: Mapped[str] = mapped_column(Text)
    text_redacted: Mapped[str] = mapped_column(Text)
    headings: Mapped[list[str]] = mapped_column(
        ARRAY(Text), default=list, server_default=sql_text("'{}'")
    )
    page_start: Mapped[int] = mapped_column(Integer)
    page_end: Mapped[int] = mapped_column(Integer)
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIM))
    tsv: Mapped[str] = mapped_column(
        TSVECTOR, Computed("to_tsvector('spanish', text)", persisted=True)
    )
    suspicious: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=sql_text("false")
    )

    document: Mapped[Document] = relationship(back_populates="chunks")

    __table_args__ = (
        Index("ix_chunks_tsv", "tsv", postgresql_using="gin"),
        Index(
            "ix_chunks_embedding",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )


class Question(Base):
    __tablename__ = "questions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    status: Mapped[QuestionStatus] = mapped_column(
        _enum(QuestionStatus), default=QuestionStatus.pending, server_default="pending"
    )
    question: Mapped[str] = mapped_column(Text)
    filters: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default=sql_text("'{}'::jsonb")
    )
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error_code: Mapped[str | None] = mapped_column(String(64))
    model: Mapped[str | None] = mapped_column(String(128))
    provider: Mapped[str | None] = mapped_column(String(32))
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    trace_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
