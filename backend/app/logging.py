import logging
from collections.abc import Iterator
from contextlib import contextmanager

import structlog
from opentelemetry import trace
from opentelemetry.trace import StatusCode
from structlog.typing import EventDict, WrappedLogger


def current_trace_id() -> str | None:
    """Hex trace id of the active OTel span, or None when there is no valid span."""
    ctx = trace.get_current_span().get_span_context()
    return trace.format_trace_id(ctx.trace_id) if ctx.is_valid else None


def _add_trace_context(_: WrappedLogger, __: str, event_dict: EventDict) -> EventDict:
    ctx = trace.get_current_span().get_span_context()
    if ctx.is_valid:
        event_dict["trace_id"] = trace.format_trace_id(ctx.trace_id)
        event_dict["span_id"] = trace.format_span_id(ctx.span_id)
    return event_dict


def configure_logging(level: str) -> None:
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            _add_trace_context,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelNamesMapping()[level.upper()]
        ),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)  # type: ignore[no-any-return]


@contextmanager
def content_free_span(tracer: trace.Tracer, name: str) -> Iterator[trace.Span]:
    """Span that records only the exception type: messages may carry document or prompt text."""
    with tracer.start_as_current_span(
        name, record_exception=False, set_status_on_exception=False
    ) as span:
        try:
            yield span
        except Exception as exc:
            span.set_status(StatusCode.ERROR)
            span.set_attribute("error.type", type(exc).__name__)
            raise
