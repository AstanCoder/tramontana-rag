"""initial schema: documents, chunks, questions

Revision ID: 0001
Revises:
"""

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

NOW = sa.text("now()")
UUID_DEFAULT = sa.text("gen_random_uuid()")


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "documents",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=UUID_DEFAULT, nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("original_filename", sa.String(255), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("doc_type", sa.String(32), nullable=False),
        sa.Column("policy_number", sa.String(64)),
        sa.Column("document_date", sa.Date()),
        sa.Column("status", sa.String(32), server_default="pending", nullable=False),
        sa.Column("error_code", sa.String(64)),
        sa.Column("page_count", sa.Integer()),
        sa.Column("is_current", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("superseded_by_id", postgresql.UUID(as_uuid=True)),
        sa.Column("storage_path", sa.String(512), nullable=False),
        sa.Column("ingest_task_id", sa.String(64)),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("processing_started_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("sha256"),
        sa.ForeignKeyConstraint(["superseded_by_id"], ["documents.id"]),
    )
    op.create_index("ix_documents_policy_number", "documents", ["policy_number"])
    op.create_index(
        "ix_documents_policy_number_current",
        "documents",
        ["policy_number"],
        postgresql_where=sa.text("is_current"),
    )

    op.create_table(
        "chunks",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=UUID_DEFAULT, nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("text_redacted", sa.Text(), nullable=False),
        sa.Column(
            "headings", postgresql.ARRAY(sa.Text()), server_default=sa.text("'{}'"), nullable=False
        ),
        sa.Column("page_start", sa.Integer(), nullable=False),
        sa.Column("page_end", sa.Integer(), nullable=False),
        sa.Column("embedding", Vector(768), nullable=False),
        sa.Column(
            "tsv",
            postgresql.TSVECTOR(),
            sa.Computed("to_tsvector('spanish', text)", persisted=True),
        ),
        sa.Column("suspicious", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_chunks_document_id", "chunks", ["document_id"])
    op.create_index("ix_chunks_tsv", "chunks", ["tsv"], postgresql_using="gin")
    op.create_index(
        "ix_chunks_embedding",
        "chunks",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_with={"m": 16, "ef_construction": 64},
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )

    op.create_table(
        "questions",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=UUID_DEFAULT, nullable=False),
        sa.Column("status", sa.String(32), server_default="pending", nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column(
            "filters", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False
        ),
        sa.Column("result", postgresql.JSONB()),
        sa.Column("error_code", sa.String(64)),
        sa.Column("model", sa.String(128)),
        sa.Column("provider", sa.String(32)),
        sa.Column("latency_ms", sa.Integer()),
        sa.Column("trace_id", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("questions")
    op.drop_table("chunks")
    op.drop_table("documents")
    op.execute("DROP EXTENSION IF EXISTS vector")
