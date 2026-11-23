from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://moderation:moderation@localhost:5432/moderation"
    redis_url: str = "redis://localhost:6379/0"
    provider_url: str = "http://localhost:8001"
    real_provider_url: str = "http://localhost:8002"
    # Which provider scores real file uploads: "real" (REAL_PROVIDER_URL) or "mock"
    # (PROVIDER_URL, so a test mock can be swapped in). JSON test documents always use PROVIDER_URL.
    moderation_provider: Literal["real", "mock"] = "real"
    media_store: str = "/media-store"
    max_image_mb: int = 25
    max_video_mb: int = 200
    max_video_seconds: int = 120
    callback_url: str = "http://localhost:8000/provider/callback"
    provider_webhook_secret: str = "change-me-in-real-life"
    screening_mode: Literal["fixed", "broken"] = "fixed"

    provider_timeout_seconds: float = 2.0
    hold_after_seconds: float = 20.0
    lease_seconds: int = 60
    workspace_inflight_cap: int = 4

    # Worker concurrency per lane, per worker process
    image_concurrency: int = 4
    video_concurrency: int = 2
    rescreen_concurrency: int = 2

    # Retry budgets (counted per job from the provider_calls ledger)
    # Retry budgets per failure type (counted per job from the provider_calls ledger)
    max_timeout_attempts: int = 3
    max_server_error_attempts: int = 4   # 5xx and no connection
    max_rate_limited_attempts: int = 5
    max_malformed_attempts: int = 2
    backoff_base_seconds: float = 0.5
    backoff_cap_seconds: float = 10.0

    # Circuit breaker
    breaker_failure_threshold: int = 5
    breaker_window_seconds: float = 30.0
    breaker_open_seconds: float = 10.0

    # Recovery
    rescreen_interval_seconds: float = 2.0
    rescreen_batch_size: int = 50       # per tick (about 25/s), so recovery does not cause a 429 storm
    rescreen_probe_window_seconds: float = 30.0
    callback_timeout_seconds: float = 120.0  # resubmit an async video if no callback by then
    cors_origins: str = "http://localhost:5173"
    log_level: str = "INFO"

    # Crash test only: the worker exits hard right after the provider answers,
    # before anything is recorded. Never set in normal runs.
    crash_after_provider: bool = False


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
