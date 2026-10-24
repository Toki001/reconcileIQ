"""Isolated PostgreSQL test schemas; never resets or truncates the application schema."""

import os
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
    values = dotenv_values(os.environ.get("RECONCILE_TEST_ENV_FILE", ".env"))
    return URL.create(
        "postgresql+psycopg",
        username=values.get("POSTGRES_USER"),
        password=values.get("POSTGRES_PASSWORD"),
        host="127.0.0.1",
        port=int(
            os.environ.get("RECONCILE_TEST_POSTGRES_PORT") or values.get("POSTGRES_PORT") or "15432"
        ),
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


@contextmanager
def postgres_database() -> Iterator[tuple[Engine, URL]]:
    """Fresh migration database on the local Compose server; remove only what we create."""
    base = local_postgres_url()
    if base.host not in {"127.0.0.1", "localhost"} or base.port != int(
        os.environ.get("RECONCILE_TEST_POSTGRES_PORT", "15432")
    ):
        raise ValueError(
            "Disposable database tests require local Compose PostgreSQL on the explicitly "
            "configured local test port"
        )
    name = "riq_m05_test_" + uuid4().hex
    control = create_engine(base, isolation_level="AUTOCOMMIT", connect_args={"connect_timeout": 3})
    database: Engine | None = None
    created = False
    try:
        with control.connect() as connection:
            connection.exec_driver_sql(f'CREATE DATABASE "{name}"')
            created = True
        url = base.set(database=name).difference_update_query(["options"])
        database = create_engine(url, connect_args={"connect_timeout": 3})
        yield database, url
    finally:
        if database is not None:
            database.dispose()
        try:
            if created:
                with control.connect() as connection:
                    connection.exec_driver_sql(f'DROP DATABASE "{name}"')
        finally:
            control.dispose()
