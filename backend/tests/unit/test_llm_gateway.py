import functools
import math
from typing import Literal

import httpx
import litellm
import pytest
from litellm.router import Router
from pydantic import BaseModel

from app.config import Settings
from app.llm.gateway import (
    LiteLLMGateway,
    LLMOutputInvalidError,
    ProviderUnavailableError,
    _retry_after,
    get_gateway,
)
from app.llm.mock import MockGateway

FLASH = "gemini/gemini-2.5-flash"
FLASH_LITE = "gemini/gemini-2.5-flash-lite"


class Citation(BaseModel):
    chunk_id: str
    quote: str


class Answer(BaseModel):
    answer: str
    citations: list[Citation]
    confidence: Literal["low", "medium", "high"]
    no_evidence: bool


def _cos(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


def _messages(question: str) -> list[dict[str, str]]:
    context = (
        '<doc-ab12 id="c1" page="1">Los daños por agua en el sótano están cubiertos. '
        "Hay franquicia.</doc-ab12>\n"
        '<doc-ab12 id="c2" page="2">El seguro de automóvil cubre la grúa.</doc-ab12>'
    )
    return [
        {"role": "system", "content": "Responde con JSON."},
        {"role": "user", "content": f"{context}\nPregunta: {question}"},
    ]


def test_mock_embed_deterministic_and_normalized() -> None:
    gw = MockGateway()
    a, b = gw.embed(["sótano inundado", "sótano inundado"])
    assert a == b
    assert len(a) == 768
    assert math.isclose(math.sqrt(sum(x * x for x in a)), 1.0)
    assert gw.embed(["sótano"]) == gw.embed(["sotano"])


def test_mock_embed_similarity_is_lexical() -> None:
    q, near, far = MockGateway().embed(
        ["daños por agua en sótano", "agua en el sótano", "seguro de automóvil"]
    )
    assert _cos(q, near) > _cos(q, far)


def test_mock_completion_cites_best_block() -> None:
    result, meta = MockGateway().complete_json(_messages("¿Cubre daños por agua?"), Answer)
    assert result.no_evidence is False
    assert [c.chunk_id for c in result.citations] == ["c1"]
    assert result.answer == result.citations[0].quote
    assert result.answer.startswith("Los daños por agua")
    assert meta.provider == "mock"


def test_mock_completion_no_overlap_is_no_evidence() -> None:
    result, _ = MockGateway().complete_json(_messages("Pregunta sobre helicópteros"), Answer)
    assert result.no_evidence is True
    assert result.citations == []


def test_mock_fail_raises_provider_unavailable() -> None:
    with pytest.raises(ProviderUnavailableError):
        MockGateway(fail=True).complete_json(_messages("agua"), Answer)
    with pytest.raises(ProviderUnavailableError):
        MockGateway(fail=True).embed(["agua"])


def test_get_gateway_defaults_to_cached_mock() -> None:
    assert isinstance(get_gateway(), MockGateway)
    assert get_gateway() is get_gateway()


def _litellm_gateway(monkeypatch: pytest.MonkeyPatch, **mock_kwargs: object) -> LiteLLMGateway:
    """Real router, but every completion is answered locally by litellm's mock layer."""
    gw = LiteLLMGateway(Settings(gemini_api_key="test-key"))  # type: ignore[arg-type]
    monkeypatch.setattr(
        gw._router, "completion", functools.partial(gw._router.completion, **mock_kwargs)
    )
    return gw


def test_router_config_from_settings() -> None:
    router = LiteLLMGateway(Settings(gemini_api_key="test-key"))._router  # type: ignore[arg-type]
    assert [m["model_name"] for m in router.model_list] == [FLASH, FLASH_LITE]
    assert router.fallbacks == [{FLASH: [FLASH_LITE]}]
    assert router.timeout == 30
    assert router.num_retries == 2
    assert router.model_list[0]["litellm_params"]["api_key"] == "test-key"


def test_litellm_primary_not_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    gw = _litellm_gateway(monkeypatch, mock_response='{"a": 1}')

    class Out(BaseModel):
        a: int

    result, meta = gw.complete_json([{"role": "user", "content": "hola"}], Out)
    assert result.a == 1
    assert (meta.provider, meta.fallback_used) == ("gemini", False)
    assert meta.input_tokens > 0 and meta.output_tokens > 0


def test_litellm_fallback_used(monkeypatch: pytest.MonkeyPatch) -> None:
    gw = _litellm_gateway(monkeypatch, mock_response='{"a": 1}', mock_testing_fallbacks=True)

    class Out(BaseModel):
        a: int

    result, meta = gw.complete_json([{"role": "user", "content": "hola"}], Out)
    assert result.a == 1
    assert meta.fallback_used is True
    assert meta.model == "gemini-2.5-flash-lite"


def test_invalid_json_raises_output_invalid(monkeypatch: pytest.MonkeyPatch) -> None:
    gw = _litellm_gateway(monkeypatch, mock_response="not json")
    with pytest.raises(LLMOutputInvalidError):
        gw.complete_json([{"role": "user", "content": "hola"}], Answer)


@pytest.mark.parametrize(
    "error",
    [
        litellm.RateLimitError("slow down", "gemini", FLASH),
        litellm.BadGatewayError("bad gateway", "gemini", FLASH),
        litellm.Timeout("timed out", FLASH, "gemini"),
    ],
    ids=["429", "502", "timeout"],
)
def test_provider_errors_mapped(monkeypatch: pytest.MonkeyPatch, error: Exception) -> None:
    gw = _litellm_gateway(monkeypatch, mock_response=error)
    with pytest.raises(ProviderUnavailableError) as exc:
        gw.complete_json([{"role": "user", "content": "hola"}], Answer)
    assert exc.value.retry_after is None


def test_retry_after_header_is_kept() -> None:
    response = httpx.Response(
        429, headers={"retry-after": "7"}, request=httpx.Request("POST", "http://x")
    )
    assert _retry_after(litellm.RateLimitError("x", "gemini", FLASH, response=response)) == 7.0


def _embedding_response(n: int, dims: int = 768) -> litellm.EmbeddingResponse:
    return litellm.EmbeddingResponse(
        model="gemini-embedding-001",
        data=[{"object": "embedding", "index": i, "embedding": [0.1] * dims} for i in range(n)],
        usage=litellm.Usage(prompt_tokens=n, total_tokens=n),
    )


@pytest.fixture(autouse=True)
def _no_retry_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Router, "_time_to_sleep_before_retry", lambda *_, **__: 0)


@pytest.fixture
def embed_calls() -> list[dict[str, object]]:
    """Records litellm.embedding calls (the Router's transport)."""
    return []


def test_embed_batches_and_checks_dimension(
    monkeypatch: pytest.MonkeyPatch, embed_calls: list[dict[str, object]]
) -> None:
    def fake_embedding(**kwargs: object) -> litellm.EmbeddingResponse:
        embed_calls.append(kwargs)
        return _embedding_response(len(kwargs["input"]))  # type: ignore[arg-type]

    monkeypatch.setattr(litellm, "embedding", fake_embedding)
    gw = LiteLLMGateway(Settings(gemini_api_key="test-key", embed_batch_size=2))  # type: ignore[arg-type]
    assert len(gw.embed(["a", "b", "c"])) == 3
    assert [len(c["input"]) for c in embed_calls] == [2, 1]  # type: ignore[arg-type]
    assert embed_calls[0]["dimensions"] == 768

    monkeypatch.setattr(litellm, "embedding", lambda **_: _embedding_response(1, dims=3))
    with pytest.raises(LLMOutputInvalidError):
        gw.embed(["a"])


def test_embed_retries_transient_error(
    monkeypatch: pytest.MonkeyPatch, embed_calls: list[dict[str, object]]
) -> None:
    def flaky(**kwargs: object) -> litellm.EmbeddingResponse:
        embed_calls.append(kwargs)
        if len(embed_calls) == 1:
            raise litellm.ServiceUnavailableError("down", "gemini", "m")
        return _embedding_response(1)

    monkeypatch.setattr(litellm, "embedding", flaky)
    gw = LiteLLMGateway(Settings(gemini_api_key="test-key"))  # type: ignore[arg-type]
    assert len(gw.embed(["a"])) == 1
    assert len(embed_calls) == 2


def test_embed_persistent_failure_is_provider_unavailable(
    monkeypatch: pytest.MonkeyPatch, embed_calls: list[dict[str, object]]
) -> None:
    def down(**kwargs: object) -> litellm.EmbeddingResponse:
        embed_calls.append(kwargs)
        raise litellm.ServiceUnavailableError("down", "gemini", "m")

    monkeypatch.setattr(litellm, "embedding", down)
    gw = LiteLLMGateway(Settings(gemini_api_key="test-key", llm_num_retries=2))  # type: ignore[arg-type]
    with pytest.raises(ProviderUnavailableError):
        gw.embed(["a"])
    assert len(embed_calls) == 3  # first attempt + num_retries
