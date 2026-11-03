from fastapi import FastAPI
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.celery import CeleryInstrumentor
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.redis import RedisInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor, SpanExporter

from app import db
from app.config import get_settings

_configured = False


def setup_telemetry(
    service_name: str, *, app: FastAPI | None = None, exporter: SpanExporter | None = None
) -> None:
    """Idempotent: the global provider and library instrumentation are set up once per process."""
    global _configured
    settings = get_settings()
    if settings.otel_sdk_disabled:
        return
    if not _configured:
        provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
        if exporter is None:
            otlp = OTLPSpanExporter(endpoint=settings.otel_exporter_otlp_endpoint or None)
            provider.add_span_processor(BatchSpanProcessor(otlp))
        else:
            provider.add_span_processor(SimpleSpanProcessor(exporter))
        trace.set_tracer_provider(provider)
        CeleryInstrumentor().instrument()  # type: ignore[no-untyped-call]
        # Records the parametrised statement only; bound values (question text) never reach spans.
        SQLAlchemyInstrumentor().instrument(engine=db.engine, enable_commenter=False)
        RedisInstrumentor().instrument()  # command arguments are masked as "?" by the library
        _configured = True
    if app is not None:
        FastAPIInstrumentor.instrument_app(app, excluded_urls="/health$")
