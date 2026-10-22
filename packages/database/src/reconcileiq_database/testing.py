"""Isolated PostgreSQL test schemas; never resets or truncates the application schema."""

from collections.abc import Iterator
from contextlib import contextmanager
from uuid import uuid4

from dotenv import dotenv_values
from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import URL, make_url
from sqlalchemy.schema import CreateSchema, DropSchema

from reconcileiq_database.config import get_settings


def local_postgres_url() -> URL:
    configured = make_url(get_settings().database_url.get_secret_value())
    if configured.get_backend_name() == "postgresql":
        return configured
    values = dotenv_values(".env")
    return URL.create(
        "postgresql+psycopg",
        username=values.get("POSTGRES_USER"),
        password=values.get("POSTGRES_PASSWORD"),
        host="127.0.0.1",
        port=int(values.get("POSTGRES_PORT") or "15432"),
        database=values.get("POSTGRES_DB"),
    )


@contextmanager
def postgres_schema() -> Iterator[tuple[Engine, URL]]:
    schema = "riq_m03_test_" + uuid4().hex
    base = local_postgres_url()
    control = create_engine(base, connect_args={"connect_timeout": 3})
    with control.begin() as connection:
        connection.execute(CreateSchema(schema))
    url = base.update_query_dict({"options": f"-csearch_path={schema}"})
    engine = create_engine(url, connect_args={"connect_timeout": 3})
    try:
        yield engine, url
    finally:
        engine.dispose()
        with control.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True))
        control.dispose()
