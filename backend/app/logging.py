import logging
import sys
from collections.abc import Iterator
from contextlib import contextmanager

import structlog
from opentelemetry import trace
from opentelemetry.trace import StatusCode
from structlog.typing import EventDict, Processor, WrappedLogger


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


def _exception_type_only(_: WrappedLogger, __: str, event_dict: EventDict) -> EventDict:
    """Library tracebacks (uvicorn, celery) keep only the type: messages may carry user content."""
    exc_info = event_dict.pop("exc_info", None)
    if isinstance(exc_info, tuple) and exc_info[0] is not None:
        event_dict["exc_type"] = exc_info[0].__name__
    return event_dict


# uvicorn sets up its own plain-text handlers (access log, tracebacks) before the app loads.
_UVICORN_LOGGERS = ("uvicorn", "uvicorn.access", "uvicorn.error")

# These log file paths, prompts or request URLs at INFO/DEBUG; only their warnings are kept.
_CONTENT_LOGGERS = (
    "docling",
    "docling_core",
    "docling_ibm_models",
    "docling_parse",
    "httpx",
    "LiteLLM",
    "LiteLLM Router",
    "LiteLLM Proxy",
)


def configure_logging(level: str) -> None:
    shared: list[Processor] = [
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        _add_trace_context,
    ]
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            *shared,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelNamesMapping()[level.upper()]
        ),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
    # Stdlib loggers (celery, libraries) go through the same JSON rendering.
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            processor=structlog.processors.JSONRenderer(),
            foreign_pre_chain=[structlog.stdlib.add_logger_name, *shared, _exception_type_only],
        )
    )
    logging.basicConfig(handlers=[handler], level=level.upper(), force=True)
    for name in _CONTENT_LOGGERS:
        library_logger = logging.getLogger(name)
        library_logger.handlers.clear()  # LiteLLM attaches its own plain-text handler
        library_logger.propagate = True
        library_logger.setLevel(logging.WARNING)
    for name in _UVICORN_LOGGERS:
        logging.getLogger(name).handlers.clear()
        logging.getLogger(name).propagate = True


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
