"""Tenant-scoped API contract persistence, extending the preserved foundation."""

import sqlalchemy as sa
from alembic import op

revision = "0002_contracts"
down_revision = "0001_foundation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "organizations",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False),
    )
    op.execute("INSERT INTO organizations (id, name) VALUES ('demo', 'Synthetic local workspace')")
    for table in ("purchase_orders", "invoices", "audit_events"):
        op.add_column(
            table,
            sa.Column("organization_id", sa.String(32), nullable=False, server_default="demo"),
        )
        op.create_index(f"ix_{table}_organization_id", table, ["organization_id"])
    for table in ("purchase_orders", "invoices"):
        op.add_column(table, sa.Column("version", sa.Integer(), nullable=False, server_default="1"))
        op.add_column(
            table,
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default="2026-10-21 00:00:00.000000",
            ),
        )
    for name in ("subtotal_minor", "tax_minor", "discount_minor"):
        op.add_column("purchase_orders", sa.Column(name, sa.BigInteger()))
    op.add_column(
        "purchase_orders", sa.Column("line_items", sa.JSON(), nullable=False, server_default="[]")
    )
    op.add_column(
        "invoices", sa.Column("synthetic", sa.Boolean(), nullable=False, server_default=sa.true())
    )
    for name, size in (("upload_key", 256), ("document_key", 256), ("document_sha256", 64)):
        op.add_column("invoices", sa.Column(name, sa.String(size)))
    op.add_column("invoices", sa.Column("document_size", sa.Integer()))
    checks = (
        (sa.CheckConstraint("amount_minor >= 0 AND amount_minor <= 9007199254740991"),)
        if op.get_bind().dialect.name == "sqlite"
        else ()
    )
    with op.batch_alter_table("invoices", table_args=checks) as batch:
        for name, kind in (
            ("vendor", sa.String(120)),
            ("currency", sa.String(3)),
            ("amount_minor", sa.BigInteger()),
            ("due_on", sa.Date()),
        ):
            batch.alter_column(name, existing_type=kind, nullable=True)
    op.create_table(
        "api_credentials",
        sa.Column("digest", sa.String(64), primary_key=True),
        sa.Column(
            "organization_id", sa.String(32), sa.ForeignKey("organizations.id"), nullable=False
        ),
        sa.Column("actor", sa.String(120), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("revoked", sa.Boolean(), nullable=False),
    )
    op.create_table(
        "idempotency_records",
        sa.Column(
            "organization_id", sa.String(32), sa.ForeignKey("organizations.id"), primary_key=True
        ),
        sa.Column("key", sa.String(128), primary_key=True),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=False),
        sa.Column("response", sa.JSON(), nullable=False),
        sa.Column("location", sa.String(256)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "jobs",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "organization_id", sa.String(32), sa.ForeignKey("organizations.id"), nullable=False
        ),
        sa.Column("invoice_id", sa.String(32), sa.ForeignKey("invoices.id")),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(64)),
        sa.Column("retryable", sa.Boolean(), nullable=False),
        sa.Column("replay_of", sa.String(32), sa.ForeignKey("jobs.id")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "outbox_events",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("job_id", sa.String(32), sa.ForeignKey("jobs.id"), nullable=False, unique=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True)),
    )
    op.create_table(
        "reconciliation_runs",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "organization_id", sa.String(32), sa.ForeignKey("organizations.id"), nullable=False
        ),
        sa.Column("invoice_id", sa.String(32), sa.ForeignKey("invoices.id"), nullable=False),
        sa.Column("job_id", sa.String(32), sa.ForeignKey("jobs.id")),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("policy_id", sa.String(40), nullable=False),
        sa.Column("outcome", sa.String(32)),
        sa.Column("synthetic", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_reconciliation_runs_invoice_id", "reconciliation_runs", ["invoice_id"])
    op.create_table(
        "discrepancies",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "organization_id", sa.String(32), sa.ForeignKey("organizations.id"), nullable=False
        ),
        sa.Column("invoice_id", sa.String(32), sa.ForeignKey("invoices.id"), nullable=False),
        sa.Column(
            "reconciliation_id",
            sa.String(32),
            sa.ForeignKey("reconciliation_runs.id"),
            nullable=False,
        ),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False),
        sa.Column("field", sa.String(80), nullable=False),
        sa.Column("expected", sa.String(200)),
        sa.Column("observed", sa.String(200)),
        sa.Column("currency", sa.String(3)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_discrepancies_reconciliation_id", "discrepancies", ["reconciliation_id"])
    op.create_table(
        "review_decisions",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "organization_id", sa.String(32), sa.ForeignKey("organizations.id"), nullable=False
        ),
        sa.Column("invoice_id", sa.String(32), sa.ForeignKey("invoices.id"), nullable=False),
        sa.Column(
            "reconciliation_id",
            sa.String(32),
            sa.ForeignKey("reconciliation_runs.id"),
            nullable=False,
        ),
        sa.Column("invoice_version", sa.Integer(), nullable=False),
        sa.Column("decision", sa.String(16), nullable=False),
        sa.Column("reason", sa.String(1000), nullable=False),
        sa.Column("actor", sa.String(120), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("invoice_id", "invoice_version"),
    )
    op.create_table(
        "goods_receipts",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "organization_id", sa.String(32), sa.ForeignKey("organizations.id"), nullable=False
        ),
        sa.Column(
            "purchase_order_id", sa.String(32), sa.ForeignKey("purchase_orders.id"), nullable=False
        ),
        sa.Column("reference", sa.String(80), nullable=False),
        sa.Column("received_on", sa.Date(), nullable=False),
        sa.Column("lines", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("organization_id", "reference"),
    )
    op.create_table(
        "webhook_receipts",
        sa.Column(
            "organization_id", sa.String(32), sa.ForeignKey("organizations.id"), primary_key=True
        ),
        sa.Column("supplier_id", sa.String(80), primary_key=True),
        sa.Column("event_id", sa.String(80), primary_key=True),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("job_id", sa.String(32), sa.ForeignKey("jobs.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    for table in (
        "api_credentials",
        "jobs",
        "reconciliation_runs",
        "discrepancies",
        "review_decisions",
        "goods_receipts",
    ):
        op.create_index(f"ix_{table}_organization_id", table, ["organization_id"])
    for table in ("invoices", "purchase_orders", "reconciliation_runs", "discrepancies"):
        op.create_index(f"ix_{table}_tenant_cursor", table, ["organization_id", "created_at", "id"])
    op.create_index(
        "ix_audit_events_tenant_cursor", "audit_events", ["organization_id", "occurred_at", "id"]
    )


def downgrade() -> None:
    raise RuntimeError(
        "Contract records and audit history must be retained; use a forward migration."
    )
