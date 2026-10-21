from datetime import date, datetime

from sqlalchemy import BigInteger, CheckConstraint, Date, DateTime, ForeignKey, String, Text, event
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class PurchaseOrder(Base):
    __tablename__ = "purchase_orders"
    __table_args__ = (CheckConstraint("amount_minor >= 0 AND amount_minor <= 9007199254740991"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    vendor: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(String(200))
    currency: Mapped[str] = mapped_column(String(3))
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    issued_on: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(32))


class Invoice(Base):
    __tablename__ = "invoices"
    __table_args__ = (CheckConstraint("amount_minor >= 0 AND amount_minor <= 9007199254740991"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    vendor: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(String(200))
    currency: Mapped[str] = mapped_column(String(3))
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    due_on: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(32), index=True)
    purchase_order_id: Mapped[str | None] = mapped_column(ForeignKey("purchase_orders.id"))
    exception_code: Mapped[str | None] = mapped_column(String(64))
    exception_detail: Mapped[str | None] = mapped_column(Text)


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    entity_id: Mapped[str] = mapped_column(String(128), index=True)
    actor: Mapped[str] = mapped_column(String(120))
    action: Mapped[str] = mapped_column(String(80))
    detail: Mapped[str] = mapped_column(Text)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class JobExecution(Base):
    __tablename__ = "job_execution_records"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    result: Mapped[str] = mapped_column(String(80))
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


@event.listens_for(AuditEvent, "before_update")
@event.listens_for(AuditEvent, "before_delete")
def prevent_audit_mutation(*args: object) -> None:
    raise ValueError("Audit events are append-only")
