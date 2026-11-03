import uuid
from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest
from alembic.config import Config
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, delete
from sqlalchemy.orm import Session
from testcontainers.community.postgres import PostgresContainer

from alembic import command
from app.db import create_db_engine, get_session
from app.ingestion.service import run_ingestion
from app.llm.mock import MockGateway
from app.models import DocType, Document

SAMPLES = Path(__file__).parents[3] / "samples"


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    # Every test here needs Docker (testcontainers) and most need Docling: `-m "not integration"`.
    here = Path(__file__).parent
    for item in items:
        if item.path.is_relative_to(here):
            item.add_marker(pytest.mark.integration)


# key -> (file, doc_type, policy_number, document_date)
SAMPLE_DOCS = {
    "v1": (
        "poliza-hogar-TRM-HOG-2024-00123.pdf",
        DocType.policy,
        "TRM-HOG-2024-00123",
        date(2024, 1, 15),
    ),
    "v2": (
        "poliza-hogar-TRM-HOG-2024-00123-v2.pdf",
        DocType.policy,
        "TRM-HOG-2024-00123",
        date(2025, 1, 15),
    ),
    "general": (
        "condicionado-general-hogar.pdf",
        DocType.general_conditions,
        None,
        date(2023, 6, 1),
    ),
    "malicious": (
        "poliza-comercio-maliciosa-TRM-COM-2024-00999.pdf",
        DocType.policy,
        "TRM-COM-2024-00999",
        date(2024, 9, 1),
    ),
}


@pytest.fixture(scope="module")
def docs(engine: Engine) -> Iterator[dict[str, uuid.UUID]]:
    """Ingest the samples once per module (Docling is slow); committed, removed on teardown."""
    ids: dict[str, uuid.UUID] = {}
    with Session(engine) as session:
        for key, (filename, doc_type, policy, doc_date) in SAMPLE_DOCS.items():
            doc = Document(
                title=key,
                original_filename=filename,
                sha256=uuid.uuid4().hex * 2,
                doc_type=doc_type,
                policy_number=policy,
                document_date=doc_date,
                storage_path=str(SAMPLES / filename),
                ingest_task_id="t",
            )
            session.add(doc)
            session.commit()
            run_ingestion(session, doc.id, "t", MockGateway())
            ids[key] = doc.id
        yield ids
        session.execute(delete(Document).where(Document.id.in_(ids.values())))
        session.commit()


@pytest.fixture(scope="session")
def pg_url() -> Iterator[str]:
    with PostgresContainer("pgvector/pgvector:pg17", driver="psycopg") as pg:
        url = pg.get_connection_url()
        cfg = Config("alembic.ini")
        cfg.set_main_option("sqlalchemy.url", url)
        command.upgrade(cfg, "head")
        yield url


@pytest.fixture(scope="session")
def engine(pg_url: str) -> Iterator[Engine]:
    engine = create_db_engine(pg_url)
    yield engine
    engine.dispose()


@pytest.fixture
def db_session(engine: Engine) -> Iterator[Session]:
    # Outer transaction is rolled back after each test; session.commit() only releases a savepoint.
    with engine.connect() as connection:
        transaction = connection.begin()
        with Session(connection, join_transaction_mode="create_savepoint") as session:
            yield session
        transaction.rollback()


@pytest.fixture
def client(app: FastAPI, db_session: Session) -> Iterator[TestClient]:
    app.dependency_overrides[get_session] = lambda: db_session
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()
