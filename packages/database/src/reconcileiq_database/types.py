"""NUMERIC in PostgreSQL; exact decimal TEXT in SQLite (never binary-float money math)."""

from decimal import Decimal
from typing import Any

from sqlalchemy import Numeric, String
from sqlalchemy.engine import Dialect
from sqlalchemy.types import TypeDecorator, TypeEngine


class ExactDecimal(TypeDecorator[Decimal]):
    impl = Numeric
    cache_ok = True

    def load_dialect_impl(self, dialect: Dialect) -> TypeEngine[Any]:
        return String(32) if dialect.name == "sqlite" else Numeric(15, 6)

    def process_bind_param(self, value: Decimal | None, dialect: Dialect) -> str | Decimal | None:
        if value is None:
            return None
        if not isinstance(value, Decimal) or not value.is_finite():
            raise ValueError("ExactDecimal requires a finite Decimal; floats are prohibited")
        if int(value.as_tuple().exponent) < -6:
            raise ValueError("At most six fractional digits are supported")
        return format(value, "f") if dialect.name == "sqlite" else value

    def process_result_value(self, value: Any, dialect: Dialect) -> Decimal | None:
        return None if value is None else Decimal(value)
