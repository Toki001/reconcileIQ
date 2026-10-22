from functools import lru_cache
from typing import Literal
from urllib.parse import urlsplit

from pydantic import AnyHttpUrl, BaseModel, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class SupplierWebhookConfig(BaseModel):
    organization_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,32}$")
    secret: SecretStr = Field(min_length=32)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="RECONCILE_", env_file=".env", extra="ignore", hide_input_in_errors=True
    )

    environment: Literal["local", "test"] = "local"
    database_url: SecretStr = SecretStr("sqlite:///.data/reconcileiq.db")
    broker_url: SecretStr = SecretStr("memory://")
    minio_endpoint: AnyHttpUrl = AnyHttpUrl("http://127.0.0.1:9000")
    infrastructure_required: bool = False
    admin_token: SecretStr = SecretStr("")
    reviewer_token: SecretStr = SecretStr("")
    auditor_token: SecretStr = SecretStr("")
    cursor_secret: SecretStr = SecretStr("")
    supplier_webhooks: dict[str, SupplierWebhookConfig] = Field(default_factory=dict)
    minio_access_key: SecretStr = SecretStr("")
    minio_secret_key: SecretStr = SecretStr("")
    minio_public_endpoint: AnyHttpUrl = AnyHttpUrl("http://127.0.0.1:9100")
    minio_bucket: str = Field(default="invoices", pattern=r"^[a-z0-9][a-z0-9-]{2,62}$")
    worker_enabled: bool = False
    worker_concurrency: int = Field(default=4, ge=1, le=16)
    worker_lease_seconds: int = Field(default=120, ge=90, le=600)
    dispatch_interval_seconds: int = Field(default=2, ge=1, le=60)
    dispatch_batch_size: int = Field(default=25, ge=1, le=100)
    dispatch_visibility_seconds: int = Field(default=180, ge=90, le=900)
    admitted_job_limit: int = Field(default=500, ge=1, le=500)
    retry_base_seconds: int = Field(default=5, ge=1, le=60)
    retry_max_seconds: int = Field(default=300, ge=5, le=3600)

    @model_validator(mode="after")
    def validate_urls(self) -> "Settings":
        database = self.database_url.get_secret_value()
        broker = self.broker_url.get_secret_value()
        for value in (
            self.admin_token,
            self.reviewer_token,
            self.auditor_token,
            self.cursor_secret,
        ):
            if value.get_secret_value() and len(value.get_secret_value()) < 32:
                raise ValueError(
                    "Configured API and cursor secrets must contain at least 32 characters"
                )
        if not database.startswith(("sqlite:///", "postgresql+psycopg://")):
            raise ValueError("Use SQLite locally or PostgreSQL with the psycopg driver")
        if urlsplit(broker).scheme not in {"amqp", "amqps", "memory"}:
            raise ValueError("Broker must use AMQP, AMQPS, or local in-memory transport")
        if (self.infrastructure_required or self.worker_enabled) and (
            not database.startswith("postgresql+psycopg://")
            or urlsplit(broker).scheme not in {"amqp", "amqps"}
        ):
            raise ValueError("Infrastructure and business workers require PostgreSQL and RabbitMQ")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
