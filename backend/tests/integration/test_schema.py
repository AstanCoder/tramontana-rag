import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from app.models import Base, Chunk, ChunkKind, DocType, Document, DocumentStatus, Question


def make_document(sha256: str = "a" * 64) -> Document:
    return Document(
        title="Póliza",
        original_filename="poliza.pdf",
        sha256=sha256,
        doc_type=DocType.policy,
        status=DocumentStatus.ready,
        storage_path="/data/uploads/poliza.pdf",
    )


def test_vector_extension_and_hnsw_index(db_session: Session) -> None:
    indexdef = db_session.scalars(
        text(
            "SELECT indexdef FROM pg_indexes WHERE tablename = 'chunks' AND indexdef ILIKE '%hnsw%'"
        )
    ).all()
    assert len(indexdef) == 1
    assert "vector_cosine_ops" in indexdef[0]

    version = db_session.scalar(
        text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
    )
    assert version is not None
    assert tuple(int(p) for p in version.split(".")) >= (0, 8, 0)


def test_tsv_generated(db_session: Session) -> None:
    doc = make_document()
    doc.chunks.append(
        Chunk(
            ordinal=0,
            kind=ChunkKind.text,
            text="Daños por agua en sótano",
            text_redacted="Daños por agua en sótano",
            page_start=1,
            page_end=1,
            embedding=[0.1] * 768,
        )
    )
    db_session.add(doc)
    db_session.commit()

    matched = db_session.scalar(
        text("SELECT tsv @@ websearch_to_tsquery('spanish', 'sótanos agua') FROM chunks")
    )
    assert matched is True


def test_sha256_unique(db_session: Session) -> None:
    db_session.add_all([make_document(), make_document()])
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_models_match_migration(engine: Engine) -> None:
    with engine.connect() as connection:
        diff = compare_metadata(MigrationContext.configure(connection), Base.metadata)
    assert diff == []


def test_sql_errors_do_not_carry_bound_values(engine: Engine) -> None:
    # Exception text reaches the auto-instrumented server span and uvicorn's log.
    question = "Marta 12345678Z"
    with Session(engine) as session:
        session.add(Question(question=question, status="x" * 40))  # status is VARCHAR(32)
        with pytest.raises(DBAPIError) as excinfo:
            session.flush()
    assert question not in str(excinfo.value)
