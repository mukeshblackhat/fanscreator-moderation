from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://moderation:moderation@localhost:5432/moderation"
    redis_url: str = "redis://localhost:6379/0"
    provider_url: str = "http://localhost:8001"
    callback_url: str = "http://localhost:8000/provider/callback"
    provider_webhook_secret: str = "change-me-in-real-life"
    screening_mode: Literal["fixed", "broken"] = "fixed"

    provider_timeout_seconds: float = 2.0
    hold_after_seconds: float = 20.0
    lease_seconds: int = 60
    workspace_inflight_cap: int = 4
    cors_origins: str = "http://localhost:5173"
    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
