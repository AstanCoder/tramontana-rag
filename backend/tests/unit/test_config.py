import pytest

from app.config import Settings


def test_csv_env_vars_are_split(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CORS_ORIGINS", "http://a.test, http://b.test")
    monkeypatch.setenv("LLM_FALLBACK_MODELS", "m1,m2")

    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.cors_origins == ["http://a.test", "http://b.test"]
    assert settings.llm_fallback_models == ["m1", "m2"]


@pytest.mark.parametrize(
    ("provider", "override", "expected"),
    [("litellm", None, 0.55), ("mock", None, 0.40), ("litellm", 0.7, 0.7), ("mock", 0.7, 0.7)],
)
def test_evidence_threshold_resolves_per_provider_unless_set(
    provider: str, override: float | None, expected: float
) -> None:
    settings = Settings(_env_file=None, llm_provider=provider, evidence_min_similarity=override)  # type: ignore[call-arg, arg-type]
    assert settings.evidence_threshold == expected
