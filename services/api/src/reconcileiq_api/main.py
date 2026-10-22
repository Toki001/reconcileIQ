from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Literal
from urllib.request import urlopen
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse
from kombu import Connection
from reconcileiq_database.config import Settings, get_settings
from reconcileiq_database.models import AuditEvent, Invoice, PurchaseOrder
from reconcileiq_database.seed import SAMPLE_DATE
from reconcileiq_database.session import build_engine
from sqlalchemy import func, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError
from starlette.exceptions import HTTPException
from starlette.middleware.base import RequestResponseEndpoint
from starlette.responses import Response
from starlette.routing import WebSocketRoute
from strawberry.fastapi import GraphQLRouter

from reconcileiq_api.auth import Actor
from reconcileiq_api.errors import ERROR_RESPONSES, ApiError, ApiProblem, FieldError
from reconcileiq_api.graphql import Context, schema
from reconcileiq_api.pagination import utc
from reconcileiq_api.rest import build_router
from reconcileiq_api.schemas import (
    AuditRead,
    DashboardSnapshot,
    Health,
    InvoiceRead,
    PurchaseOrderRead,
)
from reconcileiq_api.storage import MinioStorage, Storage
from reconcileiq_api.webhooks import build_webhook_router


def problem(
    request: Request, error: ApiError, errors: list[FieldError] | None = None
) -> JSONResponse:
    headers = {
        "X-Request-Id": getattr(request.state, "trace_id", uuid4().hex),
        "Cache-Control": "no-store",
    }
    if error.status == 401:
        headers["WWW-Authenticate"] = "HMAC" if error.code.startswith("WEBHOOK") else "Bearer"
    if error.status in {429, 503}:
        headers["Retry-After"] = "30" if error.status == 429 else "2"
    return JSONResponse(
        status_code=error.status,
        media_type="application/problem+json",
        headers=headers,
        content=ApiProblem(
            type=f"urn:reconcileiq:problem:{error.code}",
            title=error.code.replace("_", " ").title(),
            status=error.status,
            code=error.code,
            detail=error.detail,
            instance=request.url.path,
            trace_id=getattr(request.state, "trace_id", uuid4().hex),
            errors=errors or [],
        ).model_dump(),
    )


def create_app(settings: Settings | None = None, storage: Storage | None = None) -> FastAPI:
    config = settings or get_settings()
    engine = build_engine(config)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        yield
        engine.dispose()

    app = FastAPI(
        title="ReconcileIQ API",
        version="0.2.0",
        lifespan=lifespan,
        description=(
            "Tenant-scoped accounts payable API. No payments. REST owns mutations; "
            "authenticated GraphQL POST /graphql owns dashboard queries. Async 202 is "
            "durable admission, not processing completion. Money uses bounded integer minor units "
            "with explicit ISO 4217 currencies (USD, EUR, PHP, JPY, KWD). Mutations require "
            "Idempotency-Key; existing-record writes require expected_version. Lists use "
            "signed stable keyset cursors. See packages/contracts/README.md for the "
            "reviewed authentication, pagination, errors, upload and retry semantics."
        ),
    )

    def contract_openapi() -> dict[str, Any]:
        if app.openapi_schema is None:
            document = get_openapi(
                title=app.title,
                version=app.version,
                description=app.description,
                openapi_version="3.1.0",
                routes=app.routes,
            )
            admin_operations = {
                "createPurchaseOrder",
                "updatePurchaseOrder",
                "createGoodsReceipt",
                "replayJob",
            }
            for path in document["paths"].values():
                for method, operation in path.items():
                    if operation.get("security") == [{"BearerAuth": []}]:
                        operation["x-role-permissions"] = (
                            ["admin"]
                            if operation["operationId"] in admin_operations
                            else ["admin", "reviewer"]
                            if method in {"post", "patch", "put"}
                            else ["admin", "reviewer", "auditor"]
                        )
                    for status, response in operation.get("responses", {}).items():
                        headers = response.setdefault("headers", {})
                        if status == "202" or (
                            status == "201"
                            and operation["operationId"]
                            in {"requestInvoiceUpload", "createPurchaseOrder"}
                        ):
                            headers["Location"] = {
                                "description": "Created resource or job URL",
                                "schema": {"type": "string"},
                            }
                        if status in {"202", "429", "503"}:
                            headers["Retry-After"] = {
                                "description": "Suggested delay in seconds before polling/retrying",
                                "schema": {"type": "integer", "minimum": 1},
                            }
                        if method in {"post", "patch"} and status in {"200", "201", "202"}:
                            headers["Idempotency-Replayed"] = {
                                "description": "Original committed response replayed",
                                "schema": {"type": "boolean"},
                            }
                        if not headers:
                            response.pop("headers", None)
                        content = response.get("content", {})
                        if "application/problem+json" in content:
                            content.pop("application/json", None)
            app.openapi_schema = document
        return app.openapi_schema

    app.openapi = contract_openapi  # type: ignore[method-assign]
    app.openapi_version = "3.1.0"
    app.state.engine, app.state.settings = engine, config

    @app.middleware("http")
    async def context_middleware(request: Request, call_next: RequestResponseEndpoint) -> Response:
        request.state.trace_id = uuid4().hex
        if request.method in {"POST", "PATCH", "PUT"}:
            body = bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > 131_072:
                    return problem(
                        request,
                        ApiError(413, "VALIDATION_ERROR", "API request body exceeds 128 KiB."),
                    )
            request._body = bytes(body)
        response = await call_next(request)
        response.headers["X-Request-Id"] = request.state.trace_id
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(ApiError)
    async def api_error(request: Request, exc: ApiError) -> JSONResponse:
        return problem(request, exc)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        return problem(
            request,
            ApiError(422, "VALIDATION_ERROR", "Request does not match the API contract."),
            [
                FieldError(
                    path=".".join(map(str, item["loc"])), code=item["type"], message=item["msg"]
                )
                for item in exc.errors()
            ],
        )

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
        return problem(
            request,
            ApiError(
                exc.status_code,
                "NOT_FOUND" if exc.status_code == 404 else "VALIDATION_ERROR",
                "Route not found."
                if exc.status_code == 404
                else "HTTP request could not be accepted.",
            ),
        )

    @app.exception_handler(StaleDataError)
    async def stale_record(request: Request, exc: StaleDataError) -> JSONResponse:
        return problem(
            request, ApiError(409, "VERSION_CONFLICT", "Record version changed; read and retry.")
        )

    @app.exception_handler(SQLAlchemyError)
    async def database_error(request: Request, exc: SQLAlchemyError) -> JSONResponse:
        return problem(
            request,
            ApiError(
                503, "DATABASE_UNAVAILABLE", "Database unavailable. Run migrations and retry."
            ),
        )

    @app.exception_handler(Exception)
    async def internal_error(request: Request, exc: Exception) -> JSONResponse:
        return problem(request, ApiError(500, "INTERNAL_ERROR", "Request could not be completed."))

    @app.get("/health/live", response_model=Health, operation_id="getLiveness", tags=["health"])
    def live() -> Health:
        return Health(status="ok")

    @app.get(
        "/health/ready",
        response_model=Health,
        operation_id="getReadiness",
        tags=["health"],
        responses={503: {"model": Health}},
    )
    def ready() -> Health | JSONResponse:
        dependencies: dict[str, Literal["ok", "unavailable", "not_required"]] = {}
        try:
            with engine.connect() as connection:
                revision = connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar()
            dependencies["database"] = (
                "ok" if revision == "0006_processing_stage" else "unavailable"
            )
        except SQLAlchemyError:
            dependencies["database"] = "unavailable"
        for service in ("rabbitmq", "minio"):
            dependencies[service] = "not_required"
        if config.infrastructure_required:
            try:
                with Connection(config.broker_url.get_secret_value(), connect_timeout=2) as broker:
                    broker.connect()
                dependencies["rabbitmq"] = "ok"
            except Exception:
                dependencies["rabbitmq"] = "unavailable"
            try:
                with urlopen(
                    f"{str(config.minio_endpoint).rstrip('/')}/minio/health/live", timeout=2
                ) as response:
                    dependencies["minio"] = "ok" if response.status == 200 else "unavailable"
            except Exception:
                dependencies["minio"] = "unavailable"
        result = Health(
            status="unavailable" if "unavailable" in dependencies.values() else "ok",
            dependencies=dependencies,
        )
        return (
            result
            if result.status == "ok"
            else JSONResponse(status_code=503, content=result.model_dump())
        )

    @app.get(
        "/v1/demo/snapshot",
        response_model=DashboardSnapshot,
        operation_id="getDemoSnapshot",
        tags=["demo"],
        responses=ERROR_RESPONSES,
        description=(
            "Authenticated, tenant-scoped synthetic compatibility view. Bounded to 100 "
            "records per collection; business list APIs and GraphQL use signed cursor "
            "pagination."
        ),
    )
    def snapshot(actor: Actor) -> DashboardSnapshot:
        with Session(engine) as session:
            invoices = list(
                session.scalars(
                    select(Invoice)
                    .where(
                        Invoice.organization_id == actor.organization_id,
                        Invoice.synthetic.is_(True),
                    )
                    .order_by(Invoice.id)
                    .limit(100)
                )
            )
            orders = list(
                session.scalars(
                    select(PurchaseOrder)
                    .where(PurchaseOrder.organization_id == actor.organization_id)
                    .order_by(PurchaseOrder.id)
                    .limit(100)
                )
            )
            # Worker traffic must not erase the audit context of visible finance records.
            # Reserve one real event per visible entity, then fill with recent activity.
            visible = [row.id for row in invoices] + [row.id for row in orders]
            ranked = (
                select(
                    AuditEvent.id,
                    func.row_number()
                    .over(
                        partition_by=AuditEvent.entity_id,
                        order_by=(AuditEvent.occurred_at.desc(), AuditEvent.id),
                    )
                    .label("position"),
                )
                .where(
                    AuditEvent.organization_id == actor.organization_id,
                    AuditEvent.entity_id.in_(visible),
                )
                .subquery()
            )
            context = list(
                session.scalars(
                    select(AuditEvent)
                    .join(ranked, ranked.c.id == AuditEvent.id)
                    .where(ranked.c.position == 1)
                    .order_by(AuditEvent.occurred_at.desc(), AuditEvent.id)
                    .limit(100)
                )
            )
            recent = session.scalars(
                select(AuditEvent)
                .where(AuditEvent.organization_id == actor.organization_id)
                .order_by(AuditEvent.occurred_at.desc(), AuditEvent.id)
                .limit(100)
            )
            selected = {row.id: row for row in context}
            for row in recent:
                if len(selected) >= 100:
                    break
                selected.setdefault(row.id, row)
            history = sorted(
                selected.values(), key=lambda row: (-utc(row.occurred_at).timestamp(), row.id)
            )
            return DashboardSnapshot(
                as_of=SAMPLE_DATE,
                invoices=[InvoiceRead.model_validate(row) for row in invoices],
                purchase_orders=[PurchaseOrderRead.model_validate(row) for row in orders],
                audit_events=[AuditRead.model_validate(row) for row in history],
            )

    async def graphql_context(actor: Actor) -> Context:
        return Context(engine, config, actor)

    graphql = GraphQLRouter(
        schema,
        context_getter=graphql_context,
        graphql_ide=None,
        allow_queries_via_get=False,
        include_in_schema=False,
    )
    graphql.routes = [route for route in graphql.routes if not isinstance(route, WebSocketRoute)]
    app.include_router(graphql, prefix="/graphql")
    app.include_router(build_router(engine, config, storage or MinioStorage(config)))
    app.include_router(build_webhook_router(engine, config))
    return app


app = create_app()
