from sqlite3 import Connection

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import make_url

from reconcileiq_database.config import Settings


def build_engine(settings: Settings) -> Engine:
    url = settings.database_url.get_secret_value()
    if make_url(url).get_backend_name() == "sqlite":
        engine = create_engine(url, connect_args={"check_same_thread": False}, pool_pre_ping=True)

        @event.listens_for(engine, "connect")
        def enable_foreign_keys(connection: Connection, record: object) -> None:
            connection.execute("PRAGMA foreign_keys=ON")

        return engine
    return create_engine(url, connect_args={"connect_timeout": 3}, pool_pre_ping=True)
