import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app.worker import _flush_telemetry


def test_process_shutdown_flushes_batched_spans(monkeypatch: pytest.MonkeyPatch) -> None:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(BatchSpanProcessor(exporter, schedule_delay_millis=60_000))
    monkeypatch.setattr(trace, "get_tracer_provider", lambda: provider)
    provider.get_tracer("test").start_span("run/task").end()
    assert not exporter.get_finished_spans()

    _flush_telemetry()

    assert [s.name for s in exporter.get_finished_spans()] == ["run/task"]


def test_process_shutdown_is_a_noop_without_sdk_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(trace, "get_tracer_provider", trace.ProxyTracerProvider)
    _flush_telemetry()
