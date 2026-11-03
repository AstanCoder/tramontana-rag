"""documents.embedding_model: the model whose vector space the chunks live in

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Existing rows were embedded by an unrecorded model: "unknown" keeps them out of retrieval
    # until they are re-ingested.
    op.add_column(
        "documents",
        sa.Column("embedding_model", sa.String(128), server_default="unknown", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("documents", "embedding_model")
