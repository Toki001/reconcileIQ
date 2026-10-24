import json
from collections.abc import Iterator
from datetime import date, datetime
from enum import StrEnum
from typing import Any, NewType

import strawberry
from fastapi.concurrency import run_in_threadpool
from graphql import GraphQLError
from opentelemetry.trace import StatusCode
from reconcileiq_database.config import Settings
from reconcileiq_database.models import (
    AuditEvent,
    Discrepancy,
    Invoice,
    PurchaseOrder,
    ReconciliationRun,
)
from reconcileiq_telemetry import metric, stage
from sqlalchemy import Engine, func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from strawberry.extensions import (
    MaxAliasesLimiter,
    MaxTokensLimiter,
    QueryDepthLimiter,
    SchemaExtension,
)
from strawberry.fastapi import BaseContext
from strawberry.schema.config import StrawberryConfig
from strawberry.types import ExecutionContext, Info

from reconcileiq_api.auth import Principal
from reconcileiq_api.errors import ApiError
from reconcileiq_api.pagination import Page, paginate, utc

MinorUnits = NewType("MinorUnits", int)


def minor(value: Any) -> int:
    if type(value) is not int or not 0 <= value <= 9_007_199_254_740_991:
        raise GraphQLError(
            "MinorUnits requires a nonnegative safe integer.",
            extensions={"code": "VALIDATION_ERROR"},
        )
    return value


@strawberry.enum(name="Currency")
class Currency(StrEnum):
    USD = "USD"
    EUR = "EUR"
    PHP = "PHP"
    JPY = "JPY"
    KWD = "KWD"


@strawberry.enum
class InvoiceStatus(StrEnum):
    UPLOAD_REQUESTED = "upload_requested"
    PENDING_EXTRACTION = "pending_extraction"
    EXTRACTED = "extracted"
    NEEDS_REVIEW = "needs_review"
    MATCHED = "matched"
    EXCEPTION = "exception"
    DUPLICATE = "duplicate"
    REVIEWED_ACCEPTED = "reviewed_accepted"
    REVIEWED_REJECTED = "reviewed_rejected"


@strawberry.enum
class RunStatus(StrEnum):
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"


class Context(BaseContext):
    def __init__(self, engine: Engine, settings: Settings, actor: Principal) -> None:
        super().__init__()
        self.engine, self.settings, self.actor = engine, settings, actor


@strawberry.type
class Money:
    amount_minor: MinorUnits
    currency: Currency


@strawberry.type
class PageInfo:
    has_next_page: bool
    end_cursor: str | None


@strawberry.input
class InvoiceFilter:
    status: InvoiceStatus | None = None
    vendor: str | None = None


@strawberry.type(name="Invoice")
class InvoiceNode:
    id: strawberry.ID
    vendor: str | None
    description: str
    money: Money | None
    due_on: date | None
    status: InvoiceStatus
    purchase_order_id: strawberry.ID | None
    version: int
    created_at: datetime
    synthetic: bool


@strawberry.type
class InvoiceEdge:
    cursor: str
    node: InvoiceNode


@strawberry.type
class InvoiceConnection:
    edges: list[InvoiceEdge]
    page_info: PageInfo


@strawberry.type(name="PurchaseOrder")
class PurchaseOrderNode:
    id: strawberry.ID
    vendor: str
    description: str
    money: Money
    issued_on: date
    status: str
    version: int
    created_at: datetime


@strawberry.type
class PurchaseOrderEdge:
    cursor: str
    node: PurchaseOrderNode


@strawberry.type
class PurchaseOrderConnection:
    edges: list[PurchaseOrderEdge]
    page_info: PageInfo


@strawberry.type(name="Discrepancy")
class DiscrepancyNode:
    id: strawberry.ID
    invoice_id: strawberry.ID
    reconciliation_id: strawberry.ID
    code: str
    detail: str
    field: str
    expected: str | None
    observed: str | None
    currency: Currency | None


@strawberry.type
class DiscrepancyEdge:
    cursor: str
    node: DiscrepancyNode


@strawberry.type
class DiscrepancyConnection:
    edges: list[DiscrepancyEdge]
    page_info: PageInfo


def page_info(page: Page) -> PageInfo:
    return PageInfo(has_next_page=page.info.has_next_page, end_cursor=page.info.end_cursor)


def discrepancy_node(row: Discrepancy) -> DiscrepancyNode:
    return DiscrepancyNode(
        id=strawberry.ID(row.id),
        invoice_id=strawberry.ID(row.invoice_id),
        reconciliation_id=strawberry.ID(row.reconciliation_id),
        code=row.code,
        detail=row.detail,
        field=row.field,
        expected=row.expected,
        observed=row.observed,
        currency=Currency(row.currency) if row.currency else None,
    )


def discrepancy_connection(
    context: Context, first: int, after: str | None, run_id: str | None = None
) -> DiscrepancyConnection:
    with Session(context.engine) as session:
        page = paginate(
            session,
            Discrepancy,
            context.actor,
            context.settings,
            first=first,
            after=after,
            scope=json.dumps(["gql:exceptions", run_id]),
            conditions=[Discrepancy.reconciliation_id == run_id] if run_id else [],
        )
        return DiscrepancyConnection(
            edges=[
                DiscrepancyEdge(cursor=cursor, node=discrepancy_node(row))
                for row, cursor in zip(page.rows, page.cursors, strict=True)
            ],
            page_info=page_info(page),
        )


@strawberry.type
class ReconciliationResult:
    id: strawberry.ID
    invoice_id: strawberry.ID
    job_id: strawberry.ID | None
    status: RunStatus
    policy_id: str
    outcome: str | None
    created_at: datetime
    synthetic: bool

    @strawberry.field
    async def discrepancies(
        self, info: Info[Context, None], first: int = 20, after: str | None = None
    ) -> DiscrepancyConnection:
        return await run_in_threadpool(
            discrepancy_connection, info.context, first, after, str(self.id)
        )


@strawberry.type
class ReconciliationEdge:
    cursor: str
    node: ReconciliationResult


@strawberry.type
class ReconciliationConnection:
    edges: list[ReconciliationEdge]
    page_info: PageInfo


@strawberry.type(name="AuditEvent")
class AuditNode:
    id: strawberry.ID
    entity_id: strawberry.ID
    actor: str
    action: str
    detail: str
    occurred_at: datetime


@strawberry.type
class AuditEdge:
    cursor: str
    node: AuditNode


@strawberry.type
class AuditConnection:
    edges: list[AuditEdge]
    page_info: PageInfo


@strawberry.type
class CurrencyBalance:
    currency: Currency
    amount_minor: MinorUnits


@strawberry.type
class DashboardMetrics:
    invoice_count: int
    exception_count: int
    balances: list[CurrencyBalance]


def invoice_node(row: Invoice) -> InvoiceNode:
    return InvoiceNode(
        id=strawberry.ID(row.id),
        vendor=row.vendor,
        description=row.description,
        money=Money(amount_minor=MinorUnits(row.amount_minor), currency=Currency(row.currency))
        if row.amount_minor is not None and row.currency
        else None,
        due_on=row.due_on,
        status=InvoiceStatus(row.status),
        purchase_order_id=strawberry.ID(row.purchase_order_id) if row.purchase_order_id else None,
        version=row.version,
        created_at=utc(row.created_at),
        synthetic=row.synthetic,
    )


def po_node(row: PurchaseOrder) -> PurchaseOrderNode:
    return PurchaseOrderNode(
        id=strawberry.ID(row.id),
        vendor=row.vendor,
        description=row.description,
        money=Money(amount_minor=MinorUnits(row.amount_minor), currency=Currency(row.currency)),
        issued_on=row.issued_on,
        status=row.status,
        version=row.version,
        created_at=utc(row.created_at),
    )


def run_node(row: ReconciliationRun) -> ReconciliationResult:
    return ReconciliationResult(
        id=strawberry.ID(row.id),
        invoice_id=strawberry.ID(row.invoice_id),
        job_id=strawberry.ID(row.job_id) if row.job_id else None,
        status=RunStatus(row.status),
        policy_id=row.policy_id,
        outcome=row.outcome,
        created_at=utc(row.created_at),
        synthetic=row.synthetic,
    )


def one(context: Context, model: Any, resource_id: str) -> Any:
    with Session(context.engine) as session:
        return session.scalar(
            select(model).where(
                model.organization_id == context.actor.organization_id, model.id == resource_id
            )
        )


@strawberry.type
class Query:
    @strawberry.field
    async def invoices(
        self,
        info: Info[Context, None],
        first: int = 20,
        after: str | None = None,
        filter: InvoiceFilter | None = None,
    ) -> InvoiceConnection:
        def read() -> InvoiceConnection:
            conditions = []
            state, vendor = (
                filter.status.value if filter and filter.status else None,
                filter.vendor if filter else None,
            )
            if vendor is not None and len(vendor) > 120:
                raise ApiError(422, "VALIDATION_ERROR", "Vendor filter exceeds 120 characters.")
            if state:
                conditions.append(Invoice.status == state)
            if vendor:
                conditions.append(Invoice.vendor == vendor)
            with Session(info.context.engine) as session:
                page = paginate(
                    session,
                    Invoice,
                    info.context.actor,
                    info.context.settings,
                    first=first,
                    after=after,
                    scope=json.dumps(["gql:invoices", state, vendor]),
                    conditions=conditions,
                )
                return InvoiceConnection(
                    edges=[
                        InvoiceEdge(cursor=cursor, node=invoice_node(row))
                        for row, cursor in zip(page.rows, page.cursors, strict=True)
                    ],
                    page_info=page_info(page),
                )

        return await run_in_threadpool(read)

    @strawberry.field
    async def invoice(self, info: Info[Context, None], id: strawberry.ID) -> InvoiceNode | None:
        row = await run_in_threadpool(one, info.context, Invoice, str(id))
        return invoice_node(row) if row else None

    @strawberry.field
    async def purchase_orders(
        self, info: Info[Context, None], first: int = 20, after: str | None = None
    ) -> PurchaseOrderConnection:
        def read() -> PurchaseOrderConnection:
            with Session(info.context.engine) as session:
                page = paginate(
                    session,
                    PurchaseOrder,
                    info.context.actor,
                    info.context.settings,
                    first=first,
                    after=after,
                    scope="gql:purchase-orders",
                )
                return PurchaseOrderConnection(
                    edges=[
                        PurchaseOrderEdge(cursor=cursor, node=po_node(row))
                        for row, cursor in zip(page.rows, page.cursors, strict=True)
                    ],
                    page_info=page_info(page),
                )

        return await run_in_threadpool(read)

    @strawberry.field
    async def reconciliation_runs(
        self,
        info: Info[Context, None],
        invoice_id: strawberry.ID,
        first: int = 20,
        after: str | None = None,
    ) -> ReconciliationConnection:
        def read() -> ReconciliationConnection:
            with Session(info.context.engine) as session:
                page = paginate(
                    session,
                    ReconciliationRun,
                    info.context.actor,
                    info.context.settings,
                    first=first,
                    after=after,
                    scope=json.dumps(["gql:runs", invoice_id]),
                    conditions=[ReconciliationRun.invoice_id == str(invoice_id)],
                )
                return ReconciliationConnection(
                    edges=[
                        ReconciliationEdge(cursor=cursor, node=run_node(row))
                        for row, cursor in zip(page.rows, page.cursors, strict=True)
                    ],
                    page_info=page_info(page),
                )

        return await run_in_threadpool(read)

    @strawberry.field
    async def reconciliation_result(
        self, info: Info[Context, None], id: strawberry.ID
    ) -> ReconciliationResult | None:
        row = await run_in_threadpool(one, info.context, ReconciliationRun, str(id))
        return run_node(row) if row else None

    @strawberry.field
    async def exceptions(
        self, info: Info[Context, None], first: int = 20, after: str | None = None
    ) -> DiscrepancyConnection:
        return await run_in_threadpool(discrepancy_connection, info.context, first, after)

    @strawberry.field
    async def discrepancy(
        self, info: Info[Context, None], id: strawberry.ID
    ) -> DiscrepancyNode | None:
        row = await run_in_threadpool(one, info.context, Discrepancy, str(id))
        return discrepancy_node(row) if row else None

    @strawberry.field
    async def audit_timeline(
        self,
        info: Info[Context, None],
        entity_id: strawberry.ID,
        first: int = 20,
        after: str | None = None,
    ) -> AuditConnection:
        def read() -> AuditConnection:
            with Session(info.context.engine) as session:
                page = paginate(
                    session,
                    AuditEvent,
                    info.context.actor,
                    info.context.settings,
                    first=first,
                    after=after,
                    scope=json.dumps(["gql:audit", entity_id]),
                    conditions=[AuditEvent.entity_id == str(entity_id)],
                    time_field="occurred_at",
                )
                return AuditConnection(
                    edges=[
                        AuditEdge(
                            cursor=cursor,
                            node=AuditNode(
                                id=strawberry.ID(row.id),
                                entity_id=strawberry.ID(row.entity_id),
                                actor=row.actor,
                                action=row.action,
                                detail=row.detail,
                                occurred_at=utc(row.occurred_at),
                            ),
                        )
                        for row, cursor in zip(page.rows, page.cursors, strict=True)
                    ],
                    page_info=page_info(page),
                )

        return await run_in_threadpool(read)

    @strawberry.field
    async def dashboard_metrics(self, info: Info[Context, None]) -> DashboardMetrics:
        def read() -> DashboardMetrics:
            org = info.context.actor.organization_id
            with Session(info.context.engine) as session:
                count = (
                    session.scalar(
                        select(func.count())
                        .select_from(Invoice)
                        .where(Invoice.organization_id == org)
                    )
                    or 0
                )
                exceptions = (
                    session.scalar(
                        select(func.count())
                        .select_from(Discrepancy)
                        .where(Discrepancy.organization_id == org)
                    )
                    or 0
                )
                balances = session.execute(
                    select(Invoice.currency, func.sum(Invoice.amount_minor))
                    .where(
                        Invoice.organization_id == org,
                        Invoice.currency.is_not(None),
                        Invoice.amount_minor.is_not(None),
                    )
                    .group_by(Invoice.currency)
                    .order_by(Invoice.currency)
                ).all()
                return DashboardMetrics(
                    invoice_count=count,
                    exception_count=exceptions,
                    balances=[
                        CurrencyBalance(
                            currency=Currency(currency), amount_minor=MinorUnits(minor(int(amount)))
                        )
                        for currency, amount in balances
                        if currency is not None and amount is not None
                    ],
                )

        return await run_in_threadpool(read)


class SafeSchema(strawberry.Schema):
    def process_errors(
        self, errors: list[GraphQLError], execution_context: ExecutionContext | None = None
    ) -> None:
        # Do not log query variables, credentials, record identifiers or driver errors.
        for error in errors:
            cause = error.original_error
            if isinstance(cause, ApiError):
                error.message, error.extensions = cause.detail, {"code": cause.code}
            elif isinstance(cause, SQLAlchemyError):
                error.message, error.extensions = (
                    "Database is unavailable.",
                    {"code": "DEPENDENCY_UNAVAILABLE"},
                )
            elif not error.extensions:
                if cause is not None:
                    error.message = "GraphQL operation could not be completed."
                error.extensions = {
                    "code": "INTERNAL_ERROR" if cause else "GRAPHQL_VALIDATION_FAILED"
                }
            context = execution_context.context if execution_context else None
            if isinstance(context, Context) and context.request:
                error.extensions["trace_id"] = getattr(
                    context.request.state, "trace_id", "unavailable"
                )


class TelemetryExtension(SchemaExtension):
    def on_operation(self) -> Iterator[None]:
        with stage("graphql.operation") as span:
            yield
            result = self.execution_context.result
            if self.execution_context.pre_execution_errors or (result and result.errors):
                span.set_status(StatusCode.ERROR)
                metric("riq.graphql.errors")


schema = SafeSchema(
    query=Query,
    config=StrawberryConfig(
        scalar_map={
            MinorUnits: strawberry.scalar(name="MinorUnits", serialize=minor, parse_value=minor)
        }
    ),
    extensions=[
        TelemetryExtension,
        lambda: QueryDepthLimiter(max_depth=10),
        lambda: MaxTokensLimiter(max_token_count=5000),
        lambda: MaxAliasesLimiter(max_alias_count=30),
    ],
)
