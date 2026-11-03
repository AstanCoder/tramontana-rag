import time
import uuid
from collections.abc import Iterator

import pytest
from celery.contrib.testing.worker import start_worker
from fastapi.testclient import TestClient
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import format_trace_id
from sqlalchemy import Engine, delete
from sqlalchemy.orm import sessionmaker
from testcontainers.community.redis import RedisContainer

from app import db
from app.answering import tasks
from app.config import get_settings
from app.llm.mock import MockGateway
from app.main import create_app
from app.models import Question
from app.telemetry import setup_telemetry
from app.worker import celery_app

QUESTION = "¿Cuál es el deducible de daños por agua?"
TERMINAL = {"answered", "no_evidence", "failed"}
RUN_SPAN = "run/app.answering.tasks.answer_question"


@pytest.fixture(scope="module")
def redis_url() -> Iterator[str]:
    with RedisContainer("redis:7-alpine") as redis:
        yield f"redis://{redis.get_container_host_ip()}:{redis.get_exposed_port(6379)}/0"


@pytest.fixture(scope="module")
def exporter(engine: Engine) -> Iterator[InMemorySpanExporter]:
    # The global tracer provider is set once per process, so one exporter serves the module.
    span_exporter = InMemorySpanExporter()
    with pytest.MonkeyPatch.context() as mp:
        # The API and the worker thread share committed data, so both use the test engine.
        mp.setattr(db, "engine", engine)
        mp.setattr(db, "SessionLocal", sessionmaker(engine, expire_on_commit=False))
        mp.setattr(get_settings(), "otel_sdk_disabled", False)
        setup_telemetry("tramontana-test", exporter=span_exporter)
        yield span_exporter


@pytest.fixture(scope="module")
def client(
    exporter: InMemorySpanExporter, redis_url: str, docs: dict[str, uuid.UUID]
) -> Iterator[TestClient]:
    original = {k: celery_app.conf[k] for k in ("broker_url", "result_backend")}
    celery_app.conf.update(broker_url=redis_url, result_backend=redis_url)
    try:
        with (
            pytest.MonkeyPatch.context() as mp,
            start_worker(celery_app, perform_ping_check=False, pool="solo"),
        ):
            mp.setattr(tasks, "get_gateway", MockGateway)
            yield TestClient(create_app(), raise_server_exceptions=False)
    finally:
        celery_app.conf.update(original)


def _wait_for(name: str, exporter: InMemorySpanExporter) -> list[ReadableSpan]:
    # The task span ends after the worker commits, so it can lag behind the API status.
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        spans = list(exporter.get_finished_spans())
        if any(s.name == name for s in spans):
            return spans
        time.sleep(0.1)
    raise AssertionError(f"span {name} was never exported")


def test_one_trace_spans_api_worker_retrieval_and_llm(
    client: TestClient, exporter: InMemorySpanExporter, engine: Engine
) -> None:
    response = client.post("/api/questions", json={"question": QUESTION})
    assert response.status_code == 202
    question_id = response.json()["id"]
    try:
        deadline = time.monotonic() + 60
        body = client.get(f"/api/questions/{question_id}").json()
        while body["status"] not in TERMINAL and time.monotonic() < deadline:
            time.sleep(0.2)
            body = client.get(f"/api/questions/{question_id}").json()
        assert body["status"] == "answered"

        spans = _wait_for(RUN_SPAN, exporter)
        root = next(s for s in spans if s.name == "POST /api/questions")
        trace_id = root.context.trace_id
        in_trace = [s for s in spans if s.context.trace_id == trace_id]
        names = {s.name for s in in_trace}
        assert {"POST /api/questions", RUN_SPAN, "retrieval.search", "llm.completion"} <= names
        # SQL spans are in the trace, so the content check below covers their statements too.
        assert any((s.attributes or {}).get("db.system") == "postgresql" for s in in_trace)

        llm = next(s for s in in_trace if s.name == "llm.completion")
        assert llm.attributes is not None
        assert llm.attributes["gen_ai.request.model"] == "mock"
        assert "gen_ai.usage.input_tokens" in llm.attributes

        with sessionmaker(engine)() as session:
            stored = session.get(Question, uuid.UUID(question_id))
            assert stored is not None
            assert stored.trace_id == format_trace_id(trace_id)

        for span in spans:
            for value in (span.attributes or {}).values():
                assert QUESTION not in str(value), span.name
    finally:
        with sessionmaker(engine)() as session:
            session.execute(delete(Question).where(Question.id == uuid.UUID(question_id)))
            session.commit()


def test_error_envelope_carries_the_request_trace_id(
    client: TestClient, exporter: InMemorySpanExporter
) -> None:
    exporter.clear()
    response = client.get(f"/api/questions/{uuid.uuid4()}")

    assert response.status_code == 404
    server = next(
        s for s in exporter.get_finished_spans() if s.name == "GET /api/questions/{question_id}"
    )
    assert response.json()["error"]["trace_id"] == format_trace_id(server.context.trace_id)


def test_health_is_not_traced(client: TestClient, exporter: InMemorySpanExporter) -> None:
    exporter.clear()
    assert client.get("/health").status_code == 200
    assert not [s for s in exporter.get_finished_spans() if "/health" in s.name]
