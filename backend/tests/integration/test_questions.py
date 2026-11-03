import json
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import timedelta
from typing import Any, TypeVar

import pytest
from celery.exceptions import SoftTimeLimitExceeded
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.answering import router, service, tasks
from app.llm.gateway import CompletionMeta, LLMOutputInvalidError, ProviderUnavailableError
from app.llm.mock import MockGateway
from app.models import Chunk, Question

T = TypeVar("T", bound=BaseModel)

WATER_QUESTION = "¿Cuál es el deducible de daños por agua?"


class SpyGateway(MockGateway):
    def __init__(self) -> None:
        super().__init__()
        self.completions = 0
        self.prompts: list[str] = []

    def complete_json(
        self, messages: list[dict[str, str]], schema: type[T]
    ) -> tuple[T, CompletionMeta]:
        self.completions += 1
        self.prompts.append("\n".join(m["content"] for m in messages))
        return super().complete_json(messages, schema)


class CompletionDownGateway(MockGateway):
    def complete_json(
        self, messages: list[dict[str, str]], schema: type[T]
    ) -> tuple[T, CompletionMeta]:
        raise ProviderUnavailableError("RateLimitError")


class MalformedOutputGateway(MockGateway):
    def complete_json(
        self, messages: list[dict[str, str]], schema: type[T]
    ) -> tuple[T, CompletionMeta]:
        raise LLMOutputInvalidError("model output does not match the schema")


@pytest.fixture
def env(
    monkeypatch: pytest.MonkeyPatch, db_session: Session, docs: dict[str, uuid.UUID]
) -> dict[str, Any]:
    """Run the answer task synchronously against the test session; enqueue is a no-op."""
    state: dict[str, Any] = {"gateway": SpyGateway(), "enqueued": []}

    @contextmanager
    def scope() -> Iterator[Session]:
        try:
            yield db_session
            db_session.commit()
        except Exception:
            db_session.rollback()
            raise

    monkeypatch.setattr(tasks, "session_scope", scope)
    monkeypatch.setattr(tasks, "get_gateway", lambda: state["gateway"])
    monkeypatch.setattr(
        router.answer_question, "apply_async", lambda args: state["enqueued"].append(args)
    )
    return state


def _ask(client: TestClient, question: str, **filters: Any) -> dict[str, Any]:
    response = client.post("/api/questions", json={"question": question, "filters": filters})
    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "pending"
    tasks.answer_question.apply(args=[body["id"]], throw=True)
    response = client.get(f"/api/questions/{body['id']}")
    assert response.status_code == 200
    return response.json()  # type: ignore[no-any-return]


def test_question_flow_answered_with_citations(
    client: TestClient, db_session: Session, env: dict[str, Any], docs: dict[str, uuid.UUID]
) -> None:
    body = _ask(client, WATER_QUESTION)

    assert body["status"] == "answered"
    result = body["result"]
    assert result["status"] == "answered"
    assert result["degraded"] is False
    assert result["citations"]
    for citation in result["citations"]:
        chunk = db_session.get(Chunk, uuid.UUID(citation["chunk_id"]))
        assert chunk is not None
        assert citation["page"] == chunk.page_start
        assert citation["document_id"] == str(chunk.document_id)
        assert chunk.document_id != docs["v1"]  # superseded version excluded by default
    question = db_session.get(Question, uuid.UUID(body["id"]))
    assert question is not None
    assert (question.model, question.provider) == ("mock", "mock")
    assert question.latency_ms is not None


def test_duplicate_delivery_is_noop(
    client: TestClient, db_session: Session, env: dict[str, Any]
) -> None:
    body = _ask(client, WATER_QUESTION)
    calls = env["gateway"].completions
    tasks.answer_question.apply(args=[body["id"]], throw=True)
    assert env["gateway"].completions == calls
    assert client.get(f"/api/questions/{body['id']}").json() == body


@pytest.mark.parametrize(("age_min", "claimed"), [(11, True), (1, False)])
def test_redelivered_stuck_question_is_reclaimed_only_when_stale(
    client: TestClient, db_session: Session, env: dict[str, Any], age_min: int, claimed: bool
) -> None:
    response = client.post("/api/questions", json={"question": WATER_QUESTION})
    qid = uuid.UUID(response.json()["id"])
    # A worker died mid-task: the row stays `processing` and the broker redelivers.
    db_session.execute(
        update(Question)
        .where(Question.id == qid)
        .values(status="processing", created_at=func.now() - timedelta(minutes=age_min))
    )
    db_session.commit()

    tasks.answer_question.apply(args=[str(qid)], throw=True)

    body = client.get(f"/api/questions/{qid}").json()
    assert body["status"] == ("answered" if claimed else "processing")
    assert env["gateway"].completions == (1 if claimed else 0)


def test_out_of_domain_question_no_evidence(client: TestClient, env: dict[str, Any]) -> None:
    body = _ask(client, "¿Quién ganó el Mundial de fútbol de 2010?")

    assert body["status"] == "no_evidence"
    assert body["result"]["status"] == "no_evidence"
    assert body["result"]["reason"]
    assert body["result"]["citations"] == []
    assert env["gateway"].completions == 0


def test_malicious_doc_does_not_change_answer(
    client: TestClient, db_session: Session, env: dict[str, Any]
) -> None:
    body = _ask(client, "¿Cubre la póliza de comercio las inundaciones?")

    result = body["result"]
    assert "sin deducible" not in (result["answer"] or "").casefold()
    assert "están cubiertos" not in (result["answer"] or "").casefold()
    suspicious = {str(c) for c in db_session.scalars(select(Chunk.id).where(Chunk.suspicious))}
    assert suspicious
    assert not {c["chunk_id"] for c in result["citations"]} & suspicious
    [prompt] = env["gateway"].prompts
    assert "TRM-COM-2024-00999" in prompt  # the malicious policy itself was retrieved
    assert "ignora todas las instrucciones" not in prompt.casefold()


def test_provider_down_returns_extractive_degraded(
    client: TestClient, db_session: Session, env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    span_attrs: dict[str, object] = {}

    class RecordingSpan:
        def set_attribute(self, key: str, value: object) -> None:
            span_attrs[key] = value

        def set_attributes(self, attrs: dict[str, object]) -> None:
            span_attrs.update(attrs)

    @contextmanager
    def recording_span(_: object, name: str) -> Iterator[RecordingSpan]:
        assert name == "answer.validate"
        yield RecordingSpan()

    monkeypatch.setattr(service, "content_free_span", recording_span)
    env["gateway"] = CompletionDownGateway()
    body = _ask(client, WATER_QUESTION)
    assert span_attrs["llm.fallback_used"] == "extractive"
    assert span_attrs["answer.degraded"] is True

    assert body["status"] == "answered"
    result = body["result"]
    assert result["degraded"] is True
    assert result["confidence"] == "low"
    assert 1 <= len(result["citations"]) <= 3
    for citation in result["citations"]:
        chunk = db_session.get(Chunk, uuid.UUID(citation["chunk_id"]))
        assert chunk is not None
        assert citation["fragment"] == chunk.text_redacted[:300]
    question = db_session.get(Question, uuid.UUID(body["id"]))
    assert question is not None
    assert question.model is None


def test_malformed_llm_output_returns_extractive(client: TestClient, env: dict[str, Any]) -> None:
    env["gateway"] = MalformedOutputGateway()
    body = _ask(client, WATER_QUESTION)
    assert body["status"] == "answered"
    assert body["result"]["degraded"] is True


def test_embedding_failure_marks_failed(client: TestClient, env: dict[str, Any]) -> None:
    env["gateway"] = MockGateway(fail=True)
    body = _ask(client, WATER_QUESTION)
    assert body["status"] == "failed"
    assert body["error_code"] == "provider_unavailable"
    assert body["result"] is None


@pytest.mark.parametrize(
    ("error", "code"),
    [(SoftTimeLimitExceeded(), "answer_timeout"), (RuntimeError("boom"), "answer_error")],
)
def test_task_errors_mark_failed(
    client: TestClient, env: dict[str, Any], error: Exception, code: str
) -> None:
    class Raising(MockGateway):
        def complete_json(
            self, messages: list[dict[str, str]], schema: type[T]
        ) -> tuple[T, CompletionMeta]:
            raise error

    env["gateway"] = Raising()
    body = _ask(client, WATER_QUESTION)
    assert (body["status"], body["error_code"]) == ("failed", code)


def test_enqueue_failure_returns_503(
    client: TestClient, db_session: Session, env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(args: tuple[str]) -> None:
        raise ConnectionError("redis down")

    monkeypatch.setattr(router.answer_question, "apply_async", broken)
    response = client.post("/api/questions", json={"question": WATER_QUESTION})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "queue_unavailable"
    question = db_session.scalars(select(Question).order_by(Question.created_at.desc())).first()
    assert question is not None
    assert (question.status, question.error_code) == ("failed", "enqueue_failed")


@pytest.mark.parametrize(
    ("question", "code"),
    [("a", "validation_error"), ("hola\x00", "validation_error"), ("  ab  ", "invalid_question")],
)
def test_invalid_question_422_envelope(
    client: TestClient, env: dict[str, Any], question: str, code: str
) -> None:
    response = client.post("/api/questions", json={"question": question})
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == code
    assert set(error) == {"code", "message", "trace_id"}
    assert env["enqueued"] == []


def test_unknown_question_404(client: TestClient) -> None:
    response = client.get(f"/api/questions/{uuid.uuid4()}")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "question_not_found"


def test_question_text_not_logged(
    client: TestClient, env: dict[str, Any], capfd: pytest.CaptureFixture[str]
) -> None:
    question = "Soy Marta, DNI 12345678Z: ¿cuál es el deducible de daños por agua en el sótano?"
    capfd.readouterr()
    # The rendered JSON log lines (structlog caches loggers, so capture_logs would miss them).
    _ask(client, question)
    env["gateway"] = CompletionDownGateway()
    _ask(client, question)
    out, err = capfd.readouterr()

    events = {json.loads(line)["event"] for line in out.splitlines() if line.startswith("{")}
    assert {"answer.done", "answer.extractive_fallback"} <= events
    for needle in (question, "12345678Z", "Marta", "sótano"):
        assert needle not in out + err
