"""Configuration, read from the environment.

Model names are required fields with no defaults: a default here would be a model name in
code, which the project rules forbid. `.env.example` documents the values to use.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

from .errors import ConfigError


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", ".env.local"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        frozen=True,
    )

    gemini_api_key: SecretStr
    gemini_model_agent: str
    gemini_model_extract: str
    gemini_model_classify: str

    data_dir: Path = Path("data")
    ingest_concurrency: int = Field(default=4, ge=1, le=16)
    llm_max_attempts: int = Field(default=6, ge=1, le=10)
    llm_timeout_s: float = Field(default=600.0, gt=0)
    llm_base_delay_s: float = Field(default=2.0, ge=0)

    # Only sent to the API when set, because the two Gemini generations spell the knob
    # differently and sending the wrong one is a 400.
    gemini_thinking_level: str | None = None
    gemini_media_resolution: str | None = None


def load_settings() -> Settings:
    """Build settings, turning a validation failure into an actionable message."""
    try:
        return Settings()  # type: ignore[call-arg]  # values come from the environment
    except ValidationError as exc:
        missing = sorted({str(error["loc"][0]).upper() for error in exc.errors()})
        raise ConfigError(
            "Missing or invalid configuration: "
            + ", ".join(missing)
            + ". Copy .env.example to .env.local and fill it in."
        ) from exc


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return load_settings()
