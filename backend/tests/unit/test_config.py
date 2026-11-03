import pytest

from app.config import Settings


def test_csv_env_vars_are_split(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CORS_ORIGINS", "http://a.test, http://b.test")
    monkeypatch.setenv("LLM_FALLBACK_MODELS", "m1,m2")

    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.cors_origins == ["http://a.test", "http://b.test"]
    assert settings.llm_fallback_models == ["m1", "m2"]
