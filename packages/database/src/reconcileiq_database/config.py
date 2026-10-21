from functools import lru_cache
from typing import Literal
from urllib.parse import urlsplit

from pydantic import AnyHttpUrl, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="RECONCILE_", env_file=".env", extra="ignore", hide_input_in_errors=True
    )

    environment: Literal["local", "test"] = "local"
    database_url: SecretStr = SecretStr("sqlite:///.data/reconcileiq.db")
    broker_url: SecretStr = SecretStr("memory://")
    minio_endpoint: AnyHttpUrl = AnyHttpUrl("http://127.0.0.1:9000")
    infrastructure_required: bool = False

    @model_validator(mode="after")
    def validate_urls(self) -> "Settings":
        database = self.database_url.get_secret_value()
        broker = self.broker_url.get_secret_value()
        if not database.startswith(("sqlite:///", "postgresql+psycopg://")):
            raise ValueError("Use SQLite locally or PostgreSQL with the psycopg driver")
        if urlsplit(broker).scheme not in {"amqp", "amqps", "memory"}:
            raise ValueError("Broker must use AMQP, AMQPS, or local in-memory transport")
        if self.infrastructure_required and (
            not database.startswith("postgresql+psycopg://")
            or urlsplit(broker).scheme not in {"amqp", "amqps"}
        ):
            raise ValueError("Infrastructure mode requires PostgreSQL and RabbitMQ")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
