from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Literal
from urllib.request import urlopen

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from kombu import Connection
from reconcileiq_database.config import Settings, get_settings
from reconcileiq_database.models import AuditEvent, Invoice, PurchaseOrder
from reconcileiq_database.seed import SAMPLE_DATE
from reconcileiq_database.session import build_engine
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from reconcileiq_api.schemas import (
    AuditRead,
    DashboardSnapshot,
    Health,
    InvoiceRead,
    Problem,
    PurchaseOrderRead,
)


def create_app(settings: Settings | None = None) -> FastAPI:
    config = settings or get_settings()
    engine = build_engine(config)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        yield
        engine.dispose()

    app = FastAPI(
        title="ReconcileIQ local foundation",
        version="0.1.0",
        lifespan=lifespan,
        description=(
            "Read-only synthetic workspace. No auth or real financial data in milestone 01."
        ),
    )

    @app.exception_handler(SQLAlchemyError)
    async def database_error(request: Request, exc: SQLAlchemyError) -> JSONResponse:
        # Do not serialize driver exceptions: they can contain credentials and SQL values.
        return JSONResponse(
            status_code=503,
            content={
                "code": "DATABASE_UNAVAILABLE",
                "detail": "Database unavailable. Run migrations and seed.",
            },
        )

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
                version = connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar()
            dependencies["database"] = "ok" if version == "0001_foundation" else "unavailable"
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
        healthy = "unavailable" not in dependencies.values()
        result = Health(status="ok" if healthy else "unavailable", dependencies=dependencies)
        return result if healthy else JSONResponse(status_code=503, content=result.model_dump())

    @app.get(
        "/v1/demo/snapshot",
        response_model=DashboardSnapshot,
        operation_id="getDemoSnapshot",
        tags=["demo"],
        responses={503: {"model": Problem}},
        description=(
            "Bounded local fixture view: at most 100 records per collection. "
            "Not a production list API."
        ),
    )
    def snapshot() -> DashboardSnapshot:
        with Session(engine) as session:
            return DashboardSnapshot(
                as_of=SAMPLE_DATE,
                invoices=[
                    InvoiceRead.model_validate(row)
                    for row in session.scalars(select(Invoice).order_by(Invoice.id).limit(100))
                ],
                purchase_orders=[
                    PurchaseOrderRead.model_validate(row)
                    for row in session.scalars(
                        select(PurchaseOrder).order_by(PurchaseOrder.id).limit(100)
                    )
                ],
                audit_events=[
                    AuditRead.model_validate(row)
                    for row in session.scalars(
                        select(AuditEvent)
                        .order_by(AuditEvent.occurred_at.desc(), AuditEvent.id)
                        .limit(100)
                    )
                ],
            )

    return app


app = create_app()
