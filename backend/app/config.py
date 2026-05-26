from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "MarkdownEverything"
    app_env: str = "development"
    secret_key: str = "dev-secret-change-me"
    access_token_expire_minutes: int = 60 * 24 * 7

    database_url: str = "sqlite:///./markdown_everything.db"
    celery_broker_url: str = "redis://localhost:6379/0"
    celery_result_backend: str = "redis://localhost:6379/1"
    celery_always_eager: bool = False
    sync_conversions: bool = False

    storage_root: Path = Path("./data/markdown-everything")
    frontend_origin: str = "http://localhost:3000"
    backend_public_url: str = "http://localhost:8000"

    max_upload_mb: int = 500
    max_web_response_mb: int = 20
    request_timeout_seconds: float = 20
    redirect_limit: int = 5
    max_images_per_job: int = 30
    allow_rfc2544_proxy_network: bool = True

    user_concurrency_limit: int = 3
    guest_concurrency_limit: int = 1
    job_timeout_minutes: int = 60
    guest_retention_hours: int = 24
    user_retention_days: int = 7
    raw_input_retention_hours: int = 12

    ai_base_url: str = "https://api.openai.com/v1"
    ai_api_key: str = ""
    ai_model: str = "gpt-4.1-mini"
    ai_timeout_seconds: float = 45

    asr_provider: str = "local_whisper"
    local_whisper_model: str = "base"
    local_whisper_device: str = "cpu"
    local_whisper_compute_type: str = "int8"
    asr_base_url: str = ""
    asr_api_key: str = ""
    asr_model: str = "whisper-1"

    bootstrap_admin_email: str = ""
    bootstrap_admin_password: str = Field(default="", min_length=0)

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @property
    def max_web_response_bytes(self) -> int:
        return self.max_web_response_mb * 1024 * 1024


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.storage_root.mkdir(parents=True, exist_ok=True)
    return settings


settings = get_settings()
