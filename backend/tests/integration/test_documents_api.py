import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.documents import service
from app.documents.storage import StoredFile
from app.models import DocType, Document, DocumentStatus

PDF = b"%PDF-1.7\n" + b"hello"


@pytest.fixture
def upload_dir(app: FastAPI, tmp_path: Path) -> Path:
    settings = Settings(upload_dir=str(tmp_path / "uploads"))
    app.dependency_overrides[get_settings] = lambda: settings
    return Path(settings.upload_dir)


@pytest.fixture
def enqueued(monkeypatch: pytest.MonkeyPatch) -> list[uuid.UUID]:
    calls: list[uuid.UUID] = []
    monkeypatch.setattr(service, "enqueue_ingestion", lambda _s, doc: calls.append(doc.id))
    return calls


def _post(client: TestClient, content: bytes = PDF, **form: str) -> Any:
    data = {"doc_type": "policy", **form}
    return client.post(
        "/api/documents",
        files={"file": ("Poliza Hogar.pdf", content, "application/pdf")},
        data=data,
    )


def _doc(session: Session, **fields: Any) -> Document:
    defaults: dict[str, Any] = {
        "title": "t",
        "original_filename": "t.pdf",
        "sha256": uuid.uuid4().hex + uuid.uuid4().hex,
        "doc_type": DocType.policy,
        "storage_path": "/nope",
    }
    doc = Document(**{**defaults, **fields})
    session.add(doc)
    session.commit()
    return doc


def test_upload_returns_202_pending(
    client: TestClient, upload_dir: Path, enqueued: list[uuid.UUID]
) -> None:
    response = _post(client, policy_number="POL-1", document_date="2024-05-01")

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "pending"
    assert body["title"] == "Poliza Hogar"
    assert body["original_filename"] == "Poliza Hogar.pdf"
    assert body["policy_number"] == "POL-1"
    assert body["document_date"] == "2024-05-01"
    assert body["chunk_count"] == 0
    assert enqueued == [uuid.UUID(body["id"])]
    assert len(list(upload_dir.glob("*.pdf"))) == 1


def test_duplicate_upload_returns_existing_200(
    client: TestClient, upload_dir: Path, enqueued: list[uuid.UUID]
) -> None:
    first = _post(client)
    second = _post(client)

    assert second.status_code == 200
    assert second.json()["id"] == first.json()["id"]
    assert len(enqueued) == 1
    assert len(list(upload_dir.glob("*.pdf"))) == 1


def test_concurrent_duplicate_race_returns_existing(engine: Engine, tmp_path: Path) -> None:
    def stored(name: str) -> StoredFile:
        path = tmp_path / name
        path.write_bytes(PDF)
        return StoredFile(path=path, sha256="ab" * 32, size=len(PDF), original_filename="a.pdf")

    first, second = stored("1.pdf"), stored("2.pdf")
    with Session(engine) as s1, Session(engine) as s2:
        try:
            doc1, created1 = service.create_or_get_document(
                s1, first, DocType.policy, None, None, "a"
            )
            doc2, created2 = service.create_or_get_document(
                s2, second, DocType.policy, None, None, "a"
            )

            assert (created1, created2) == (True, False)
            assert doc2.id == doc1.id
            assert first.path.exists()
            assert not second.path.exists()
        finally:
            s1.rollback()
            s1.query(Document).filter_by(sha256=first.sha256).delete()
            s1.commit()


def test_list_filters_by_doc_type_and_policy(client: TestClient, db_session: Session) -> None:
    keep = _doc(db_session, policy_number="P-1", doc_type=DocType.policy)
    _doc(db_session, policy_number="P-2", doc_type=DocType.policy)
    _doc(db_session, policy_number="P-1", doc_type=DocType.claim_letter)

    response = client.get("/api/documents", params={"doc_type": "policy", "policy_number": "P-1"})

    assert [d["id"] for d in response.json()["items"]] == [str(keep.id)]
    assert client.get("/api/documents", params={"status": "ready"}).json() == {"items": []}


def test_get_unknown_returns_404_envelope(client: TestClient) -> None:
    response = client.get(f"/api/documents/{uuid.uuid4()}")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "document_not_found"


def test_retry_only_from_failed_or_stale(
    client: TestClient, db_session: Session, enqueued: list[uuid.UUID]
) -> None:
    now = datetime.now(UTC)
    ready = _doc(db_session, status=DocumentStatus.ready)
    failed = _doc(db_session, status=DocumentStatus.failed, error_code="corrupt_document")
    fresh = _doc(db_session, status=DocumentStatus.processing, processing_started_at=now)
    stale = _doc(
        db_session,
        status=DocumentStatus.processing,
        processing_started_at=now - timedelta(minutes=16),
    )

    assert client.post(f"/api/documents/{ready.id}/retry").status_code == 409
    assert client.post(f"/api/documents/{fresh.id}/retry").status_code == 409
    assert enqueued == []

    retried = client.post(f"/api/documents/{failed.id}/retry")
    assert retried.status_code == 200
    assert retried.json()["status"] == "pending"
    assert retried.json()["error_code"] is None
    assert client.post(f"/api/documents/{stale.id}/retry").status_code == 200
    assert enqueued == [failed.id, stale.id]
    assert client.post(f"/api/documents/{uuid.uuid4()}/retry").status_code == 404


def test_file_endpoint_serves_inline_pdf(
    client: TestClient, db_session: Session, tmp_path: Path
) -> None:
    path = tmp_path / "x.pdf"
    path.write_bytes(PDF)
    doc = _doc(db_session, storage_path=str(path))

    response = client.get(f"/api/documents/{doc.id}/file")

    assert response.status_code == 200
    assert response.content == PDF
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-disposition"] == "inline"
    assert response.headers["x-content-type-options"] == "nosniff"


def test_missing_doc_type_is_validation_error(client: TestClient, upload_dir: Path) -> None:
    response = client.post("/api/documents", files={"file": ("a.pdf", PDF, "application/pdf")})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
