from decimal import Decimal
from sqlite3 import Connection

from reconcileiq_reconciliation.accounting import exact_quantity, exact_rate, tax_minor
from reconcileiq_reconciliation.money import line_total_minor
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
            connection.create_function(
                "riq_line_minor",
                2,
                lambda quantity, price: line_total_minor(exact_quantity(Decimal(quantity)), price),
                deterministic=True,
            )
            connection.create_function(
                "riq_tax_minor",
                4,
                lambda gross, discount, rate, mode: tax_minor(gross, discount, Decimal(rate), mode),
                deterministic=True,
            )
            connection.create_function(
                "riq_quantity_valid",
                1,
                lambda quantity: int(exact_quantity(Decimal(quantity)) > 0),
                deterministic=True,
            )
            connection.create_function(
                "riq_rate_valid",
                1,
                lambda rate: int(exact_rate(Decimal(rate)) >= 0),
                deterministic=True,
            )

        return engine
    return create_engine(url, connect_args={"connect_timeout": 3}, pool_pre_ping=True)
