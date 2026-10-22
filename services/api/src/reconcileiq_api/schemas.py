from datetime import UTC, date, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

MoneyMinor = Annotated[int, Field(strict=True, ge=0, le=9_007_199_254_740_991)]
Currency = Literal["USD", "EUR", "PHP", "JPY", "KWD"]
InvoiceStatus = Literal[
    "matched", "exception", "needs_review", "duplicate", "reviewed_accepted", "reviewed_rejected"
]


class ReadModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class InvoiceRead(ReadModel):
    id: str
    vendor: str
    description: str
    currency: Currency
    amount_minor: MoneyMinor
    due_on: date
    status: InvoiceStatus
    purchase_order_id: str | None
    exception_code: str | None
    exception_detail: str | None


class PurchaseOrderRead(ReadModel):
    id: str
    vendor: str
    description: str
    currency: Currency
    amount_minor: MoneyMinor
    issued_on: date
    status: Literal["open", "received", "cancelled"]


class AuditRead(ReadModel):
    id: str
    entity_id: str
    actor: str
    action: str
    detail: str
    occurred_at: datetime

    @field_validator("occurred_at")
    @classmethod
    def normalize_utc(cls, value: datetime) -> datetime:
        # SQLite drops timezone metadata; seeded/worker timestamps are always UTC.
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class DashboardSnapshot(BaseModel):
    synthetic: Literal[True] = True
    as_of: date
    invoices: list[InvoiceRead] = Field(max_length=100)
    purchase_orders: list[PurchaseOrderRead] = Field(max_length=100)
    audit_events: list[AuditRead] = Field(max_length=100)


class Health(BaseModel):
    status: Literal["ok", "unavailable"]
    service: Literal["api"] = "api"
    dependencies: dict[str, Literal["ok", "unavailable", "not_required"]] = Field(
        default_factory=dict
    )


class Problem(BaseModel):
    code: str
    detail: str
