"""Add candidate extraction requests, immutable matching evidence and free-provider budgets."""

import json
from datetime import UTC, datetime
from uuid import NAMESPACE_URL, uuid5

import sqlalchemy as sa
from alembic import op

revision = "0007_invoice_workflows"
down_revision = "0006_processing_stage"
branch_labels = None
depends_on = None
HISTORY = ("structured_extraction_requests", "reconciliation_evidence", "provider_usage")


def upgrade() -> None:
    # Native ADD COLUMN preserves the existing SQLite financial guard triggers.
    op.add_column("purchase_orders", sa.Column("reference", sa.String(80), nullable=True))
    op.create_index(
        "uq_purchase_orders_organization_id_reference",
        "purchase_orders",
        ["organization_id", "reference"],
        unique=True,
    )
    # Only existing deterministic fixture IDs have a known external reference. No inference
    # from arbitrary descriptions and no mutation of old financial columns or audit rows.
    for index in range(1, 101):
        reference = f"SYN-PO-{index:04}"
        identity = uuid5(NAMESPACE_URL, "reconcileiq:" + reference).hex
        changed = (
            op.get_bind()
            .execute(
                sa.text(
                    "UPDATE purchase_orders SET reference=:reference "
                    "WHERE organization_id='synthetic-finance' AND id=:id "
                    "AND reference IS NULL RETURNING id"
                ),
                {"reference": reference, "id": identity},
            )
            .scalar()
        )
        if changed:
            op.get_bind().execute(
                sa.text(
                    "INSERT INTO audit_events "
                    "(id,organization_id,entity_id,actor,action,detail,occurred_at) "
                    "VALUES (:id,'synthetic-finance',:entity,'migration-0007',"
                    "'purchase_order_reference_backfilled',:detail,:occurred_at)"
                ),
                {
                    "id": uuid5(NAMESPACE_URL, "reconcileiq:migration-0007:" + identity).hex,
                    "entity": identity,
                    "detail": json.dumps({"reference": reference}),
                    "occurred_at": datetime.now(UTC),
                },
            )
    op.create_table(
        "structured_extraction_requests",
        sa.Column("job_id", sa.String(32), primary_key=True),
        sa.Column(
            "organization_id", sa.String(32), sa.ForeignKey("organizations.id"), nullable=False
        ),
        sa.Column("invoice_id", sa.String(32), nullable=False),
        sa.Column("text_version_id", sa.String(32), nullable=False),
        sa.Column("invoice_version", sa.Integer(), nullable=False),
        sa.Column("request_id", sa.String(32), nullable=False),
        sa.Column("model", sa.String(80), nullable=False),
        sa.Column("prompt_version", sa.String(40), nullable=False),
        sa.Column("synthetic_authorized", sa.Boolean(), nullable=False),
        sa.Column("requested_by", sa.String(120), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id", "job_id"],
            ["jobs.organization_id", "jobs.id"],
            name="fk_structured_extraction_requests_job_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "invoice_id"],
            ["invoices.organization_id", "invoices.id"],
            name="fk_structured_extraction_requests_invoice_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "text_version_id", "invoice_id"],
            [
                "extraction_versions.organization_id",
                "extraction_versions.id",
                "extraction_versions.invoice_id",
            ],
            name="fk_structured_extraction_requests_text_invoice",
        ),
    )
    op.create_table(
        "reconciliation_evidence",
        sa.Column("reconciliation_id", sa.String(32), primary_key=True),
        sa.Column(
            "organization_id", sa.String(32), sa.ForeignKey("organizations.id"), nullable=False
        ),
        sa.Column("invoice_id", sa.String(32), nullable=False),
        sa.Column("extraction_version_id", sa.String(32), nullable=False),
        sa.Column("invoice_version", sa.Integer(), nullable=False),
        sa.Column("context_sha256", sa.String(64), nullable=False),
        sa.Column("context", sa.JSON(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id", "reconciliation_id", "invoice_id"],
            [
                "reconciliation_runs.organization_id",
                "reconciliation_runs.id",
                "reconciliation_runs.invoice_id",
            ],
            name="fk_reconciliation_evidence_run_invoice",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "extraction_version_id", "invoice_id"],
            [
                "extraction_versions.organization_id",
                "extraction_versions.id",
                "extraction_versions.invoice_id",
            ],
            name="fk_reconciliation_evidence_extraction_invoice",
        ),
    )
    op.create_table(
        "provider_budgets",
        sa.Column("project_id", sa.String(64), primary_key=True),
        sa.Column("minute_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("day_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("minute_used", sa.Integer(), nullable=False),
        sa.Column("day_used", sa.Integer(), nullable=False),
        sa.Column("leases", sa.JSON(), nullable=False),
        sa.CheckConstraint("minute_used >= 0 AND day_used >= 0", name="ck_provider_budgets_usage"),
    )
    op.create_table(
        "provider_usage",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "organization_id", sa.String(32), sa.ForeignKey("organizations.id"), nullable=False
        ),
        sa.Column("job_id", sa.String(32), nullable=True),
        sa.Column("operation", sa.String(16), nullable=False),
        sa.Column("model", sa.String(80), nullable=False),
        sa.Column("prompt_version", sa.String(40), nullable=False),
        sa.Column("proof", sa.JSON(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("free_tier", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id", "job_id"],
            ["jobs.organization_id", "jobs.id"],
            name="fk_provider_usage_job_tenant",
        ),
        sa.CheckConstraint(
            "(input_tokens IS NULL OR input_tokens >= 0) AND "
            "(output_tokens IS NULL OR output_tokens >= 0)",
            name="ck_provider_usage_tokens",
        ),
        sa.CheckConstraint(
            "(operation='verification' AND job_id IS NULL) OR "
            "(operation='generation' AND job_id IS NOT NULL)",
            name="ck_provider_usage_operation",
        ),
    )
    for table in HISTORY:
        op.create_index(f"ix_{table}_organization_id", table, ["organization_id"])
        for operation in ("UPDATE", "DELETE"):
            immutable(table, operation)
        if op.get_bind().dialect.name == "postgresql":
            immutable(table, "TRUNCATE")


def immutable(table: str, operation: str) -> None:
    name = table + "_no_" + operation.lower()
    if op.get_bind().dialect.name == "sqlite":
        op.execute(
            f"CREATE TRIGGER {name} BEFORE {operation} ON {table} "
            "BEGIN SELECT RAISE(ABORT, 'Workflow evidence is immutable'); END"
        )
    else:
        op.execute(
            f"CREATE FUNCTION {name}_fn() RETURNS trigger LANGUAGE plpgsql AS $$ "
            "BEGIN RAISE EXCEPTION 'Workflow evidence is immutable' USING ERRCODE='23514'; END; $$"
        )
        level = "STATEMENT" if operation == "TRUNCATE" else "ROW"
        op.execute(
            f"CREATE TRIGGER {name} BEFORE {operation} ON {table} "
            f"FOR EACH {level} EXECUTE FUNCTION {name}_fn()"
        )


def downgrade() -> None:
    for table in (*HISTORY, "provider_budgets"):
        if op.get_bind().scalar(sa.text(f"SELECT count(*) FROM {table}")):
            raise RuntimeError(
                "Milestone 06 records are in use; "
                "preserve published history with a forward migration"
            )
    if op.get_bind().scalar(
        sa.text("SELECT count(*) FROM purchase_orders WHERE reference IS NOT NULL")
    ):
        raise RuntimeError(
            "Purchase order references are in use; "
            "preserve published history with a forward migration"
        )
    for table in reversed((*HISTORY, "provider_budgets")):
        op.drop_table(table)
        if op.get_bind().dialect.name == "postgresql" and table in HISTORY:
            for operation in ("update", "delete", "truncate"):
                op.execute(f"DROP FUNCTION {table}_no_{operation}_fn()")
    op.drop_index("uq_purchase_orders_organization_id_reference", table_name="purchase_orders")
    op.drop_column("purchase_orders", "reference")
