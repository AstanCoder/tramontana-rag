from functools import lru_cache
from typing import Annotated, Literal

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://tramontana:tramontana@localhost:5432/tramontana"
    redis_url: str = "redis://localhost:6379/0"

    upload_dir: str = "/data/uploads"
    max_upload_mb: int = 25
    max_pages: int = 200

    llm_provider: Literal["mock", "litellm"] = "mock"
    gemini_api_key: SecretStr = SecretStr("")
    llm_model: str = "gemini/gemini-2.5-flash"
    llm_fallback_models: Annotated[list[str], NoDecode] = ["gemini/gemini-2.5-flash-lite"]
    embedding_model: str = "gemini/gemini-embedding-001"
    llm_timeout_s: int = 30
    llm_num_retries: int = 2
    embed_batch_size: int = 50
    evidence_min_similarity: float = 0.55

    cors_origins: Annotated[list[str], NoDecode] = [
        "http://localhost:5173",
        "http://localhost:8080",
    ]

    otel_exporter_otlp_endpoint: str = ""
    otel_sdk_disabled: bool = True
    log_level: str = "INFO"

    @field_validator("llm_fallback_models", "cors_origins", mode="before")
    @classmethod
    def _split_csv(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
