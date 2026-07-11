"""Environment-backed application settings."""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings shared by API and worker processes."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_env: str = "development"
    log_level: str = "INFO"
    database_url: str = "postgresql+asyncpg://agents:local-development-only@postgres:5432/agents"
    temporal_address: str = "temporal:7233"
    temporal_namespace: str = "default"
    dependency_timeout_seconds: float = Field(default=3.0, gt=0, le=30)
    max_request_body_bytes: int = Field(default=65_536, ge=1_024, le=1_048_576)
    otel_exporter_otlp_endpoint: str | None = None
    otel_service_name: str = "agents-control-plane-api"
    nvidia_api_key: str | None = None
    nvidia_base_url: str = "https://integrate.api.nvidia.com/v1"
    nvidia_model: str = "mistralai/mistral-medium-3.5-128b"
    nvidia_embedding_model: str = "nvidia/llama-nemotron-embed-1b-v2"
    nvidia_timeout_seconds: float = Field(default=30.0, gt=0, le=120)
    model_max_output_tokens: int = Field(default=256, ge=1, le=2048)
    model_provider: str = "deterministic"


@lru_cache
def get_settings() -> Settings:
    return Settings()
