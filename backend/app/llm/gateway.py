"""LLM gateway: the only entry point for embeddings and structured completions."""

import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Protocol, TypeVar

from litellm.exceptions import (
    APIConnectionError,
    BadGatewayError,
    InternalServerError,
    RateLimitError,
    ServiceUnavailableError,
    Timeout,
)
from litellm.router import Router
from opentelemetry import trace
from pydantic import BaseModel

from app.config import Settings, get_settings
from app.logging import content_free_span

EMBEDDING_DIMENSIONS = 768

T = TypeVar("T", bound=BaseModel)

_tracer = trace.get_tracer("app.llm")

_PROVIDER_ERRORS = (
    RateLimitError,
    Timeout,
    APIConnectionError,
    ServiceUnavailableError,
    InternalServerError,
    BadGatewayError,
)


class ProviderUnavailableError(Exception):
    """All models/retries exhausted, timeout, or 429 after retries."""

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class LLMOutputInvalidError(Exception):
    """The model output was not valid JSON for the requested schema."""


@dataclass(frozen=True)
class CompletionMeta:
    model: str
    provider: str
    input_tokens: int
    output_tokens: int
    fallback_used: bool


class Gateway(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]]: ...

    def complete_json(
        self, messages: list[dict[str, str]], schema: type[T]
    ) -> tuple[T, CompletionMeta]: ...


@contextmanager
def llm_span(name: str, request_model: str) -> Iterator[Callable[[CompletionMeta], None]]:
    """Span without prompt content; the caller reports the outcome via the yielded callback."""
    started = time.perf_counter()
    with content_free_span(_tracer, name) as span:
        span.set_attribute("gen_ai.request.model", request_model)

        def record(meta: CompletionMeta) -> None:
            span.set_attributes(
                {
                    "gen_ai.system": meta.provider,
                    "gen_ai.response.model": meta.model,
                    "gen_ai.usage.input_tokens": meta.input_tokens,
                    "gen_ai.usage.output_tokens": meta.output_tokens,
                    "llm.provider": meta.provider,
                    "llm.fallback_used": meta.fallback_used,
                    "llm.latency_ms": round((time.perf_counter() - started) * 1000),
                }
            )

        yield record


def _provider(model: str) -> str:
    return model.split("/", 1)[0] if "/" in model else "unknown"


def _retry_after(exc: Exception) -> float | None:
    headers = getattr(getattr(exc, "response", None), "headers", None)
    try:
        return float(headers["retry-after"]) if headers else None
    except (KeyError, ValueError):
        return None  # missing header or HTTP-date form: callers fall back to their own backoff


class LiteLLMGateway:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._models = [settings.llm_model, *settings.llm_fallback_models]
        api_key = settings.gemini_api_key.get_secret_value()
        self._router = Router(
            model_list=[self._deployment(m, api_key) for m in self._models],
            fallbacks=[{settings.llm_model: settings.llm_fallback_models}],
            timeout=settings.llm_timeout_s,
            num_retries=settings.llm_num_retries,
        )
        # litellm.embedding ignores num_retries; Router.embedding retries with backoff.
        self._embed_router = Router(
            model_list=[self._deployment(settings.embedding_model, api_key)],
            timeout=settings.llm_timeout_s,
            num_retries=settings.llm_num_retries,
        )

    @staticmethod
    def _deployment(model: str, api_key: str) -> dict[str, Any]:
        return {"model_name": model, "litellm_params": {"model": model, "api_key": api_key}}

    def embed(self, texts: list[str]) -> list[list[float]]:
        s = self._settings
        provider = _provider(s.embedding_model)
        vectors: list[list[float]] = []
        input_tokens = 0
        response_model = s.embedding_model
        with llm_span("llm.embed", s.embedding_model) as record:
            for i in range(0, len(texts), s.embed_batch_size):
                response = self._call(
                    self._embed_router.embedding,
                    model=s.embedding_model,
                    input=texts[i : i + s.embed_batch_size],
                    dimensions=EMBEDDING_DIMENSIONS,
                )
                vectors.extend(item["embedding"] for item in response.data)
                input_tokens += response.usage.prompt_tokens
                response_model = response.model or response_model
            if any(len(v) != EMBEDDING_DIMENSIONS for v in vectors):
                raise LLMOutputInvalidError("embedding dimension mismatch")
            record(CompletionMeta(response_model, provider, input_tokens, 0, False))
        return vectors

    def complete_json(
        self, messages: list[dict[str, str]], schema: type[T]
    ) -> tuple[T, CompletionMeta]:
        primary = self._settings.llm_model
        with llm_span("llm.completion", primary) as record:
            response = self._call(
                self._router.completion, model=primary, messages=messages, response_format=schema
            )
            # The response carries the bare model name (no provider prefix).
            used = next((m for m in self._models if m.split("/")[-1] == response.model), None)
            used = used or response.model
            meta = CompletionMeta(
                model=response.model,
                provider=_provider(used),
                input_tokens=response.usage.prompt_tokens,
                output_tokens=response.usage.completion_tokens,
                fallback_used=used != primary,
            )
            record(meta)
        try:
            return schema.model_validate_json(response.choices[0].message.content or ""), meta
        except ValueError as exc:  # pydantic ValidationError and JSON errors
            raise LLMOutputInvalidError("model output does not match the schema") from exc

    @staticmethod
    def _call(fn: Callable[..., Any], **kwargs: Any) -> Any:
        try:
            return fn(**kwargs)
        except _PROVIDER_ERRORS as exc:
            raise ProviderUnavailableError(type(exc).__name__, _retry_after(exc)) from exc


@lru_cache
def get_gateway() -> Gateway:
    settings = get_settings()
    if settings.llm_provider == "litellm":
        return LiteLLMGateway(settings)
    from app.llm.mock import MockGateway  # mock imports this module; avoid a cycle

    return MockGateway()
