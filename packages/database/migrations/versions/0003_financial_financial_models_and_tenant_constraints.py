"""Expand financial records, preserve legacy projections and enforce tenant relationships."""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import NAMESPACE_URL, uuid5

import sqlalchemy as sa
from alembic import op

revision = "0003_financial"
down_revision = "0002_contracts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _arithmetic()
    sqlite = op.get_bind().dialect.name == "sqlite"
    op.create_table(
        "users",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("subject", sa.String(length=160), nullable=False),
        sa.Column("display_name", sa.String(length=120), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("subject"),
    )
    op.create_table(
        "organization_memberships",
        sa.Column("organization_id", sa.String(length=32), nullable=False),
        sa.Column("user_id", sa.String(length=32), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "role IN ('admin','reviewer','auditor')", name="ck_organization_memberships_role"
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("organization_id", "user_id"),
        sa.UniqueConstraint(
            "organization_id",
            "user_id",
            "role",
            name="uq_organization_memberships_organization_id_user_id_role",
        ),
    )
    op.create_table(
        "tolerance_policies",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("organization_id", sa.String(length=32), nullable=False),
        sa.Column("code", sa.String(length=40), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "currency IN ('USD','EUR','PHP','JPY','KWD')", name="ck_tolerance_policies_currency"
        ),
        sa.CheckConstraint(
            "amount_minor >= 0 AND amount_minor <= 9007199254740991",
            name="ck_tolerance_policies_amount_minor",
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id",
            "code",
            "currency",
            "version",
            name="uq_tolerance_policies_organization_id_code_currency_version",
        ),
        sa.UniqueConstraint(
            "organization_id", "id", name="uq_tolerance_policies_organization_id_id"
        ),
        sa.CheckConstraint("version >= 1", name="ck_tolerance_policies_version"),
    )
    op.create_index(
        op.f("ix_tolerance_policies_organization_id"),
        "tolerance_policies",
        ["organization_id"],
        unique=False,
    )
    op.create_table(
        "vendors",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("organization_id", sa.String(length=32), nullable=False),
        sa.Column("code", sa.String(length=80), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("normalized_name", sa.String(length=120), nullable=False),
        sa.Column("default_currency", sa.String(length=3), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "default_currency IN ('USD','EUR','PHP','JPY','KWD')", name="ck_vendors_currency"
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "code", name="uq_vendors_organization_id_code"),
        sa.UniqueConstraint("organization_id", "id", name="uq_vendors_organization_id_id"),
        sa.UniqueConstraint(
            "organization_id",
            "normalized_name",
            name="uq_vendors_organization_id_normalized_name",
        ),
    )
    op.create_index(
        op.f("ix_vendors_organization_id"), "vendors", ["organization_id"], unique=False
    )
    with op.batch_alter_table(
        "api_credentials",
        naming_convention={("pk"): "pk_%(table_name)s"},
        reflect_kwargs={("resolve_fks"): False},
    ) as batch:
        batch.add_column(sa.Column("user_id", sa.String(length=32), nullable=True))
        if sqlite:
            batch.create_foreign_key(
                "fk_api_credentials_membership",
                "organization_memberships",
                ["organization_id", "user_id", "role"],
                ["organization_id", "user_id", "role"],
            )
    with op.batch_alter_table(
        "audit_events",
        naming_convention={("pk"): "pk_%(table_name)s"},
        reflect_kwargs={("resolve_fks"): False},
    ) as batch:
        batch.create_unique_constraint(
            "uq_audit_events_organization_id_id", ["organization_id", "id"]
        )
        if sqlite:
            batch.create_foreign_key(
                "fk_audit_events_organization", "organizations", ["organization_id"], ["id"]
            )
    with op.batch_alter_table(
        "discrepancies",
        naming_convention={("pk"): "pk_%(table_name)s"},
        reflect_kwargs={("resolve_fks"): False},
    ) as batch:
        batch.create_unique_constraint(
            "uq_discrepancies_organization_id_id", ["organization_id", "id"]
        )
        if sqlite:
            batch.create_foreign_key(
                "fk_discrepancies_invoice_tenant",
                "invoices",
                ["organization_id", "invoice_id"],
                ["organization_id", "id"],
            )
            batch.create_foreign_key(
                "fk_discrepancies_run_invoice",
                "reconciliation_runs",
                ["organization_id", "reconciliation_id", "invoice_id"],
                ["organization_id", "id", "invoice_id"],
            )
    with op.batch_alter_table(
        "goods_receipts",
        naming_convention={("pk"): "pk_%(table_name)s"},
        reflect_kwargs={("resolve_fks"): False},
    ) as batch:
        batch.add_column(
            sa.Column("lines_normalized", sa.Boolean(), server_default="false", nullable=False)
        )
        batch.create_unique_constraint(
            "uq_goods_receipts_organization_id_id", ["organization_id", "id"]
        )
        batch.create_unique_constraint(
            "uq_goods_receipts_organization_id_id_purchase_order_id",
            ["organization_id", "id", "purchase_order_id"],
        )
        if sqlite:
            batch.create_foreign_key(
                "fk_goods_receipts_po_tenant",
                "purchase_orders",
                ["organization_id", "purchase_order_id"],
                ["organization_id", "id"],
            )
    with op.batch_alter_table(
        "invoices",
        table_args=(sa.CheckConstraint("amount_minor >= 0 AND amount_minor <= 9007199254740991"),),
        naming_convention={("pk"): "pk_%(table_name)s"},
        reflect_kwargs={("resolve_fks"): False},
    ) as batch:
        batch.add_column(sa.Column("vendor_id", sa.String(length=32), nullable=True))
        batch.add_column(sa.Column("supplier_invoice_number", sa.String(length=80), nullable=True))
        batch.add_column(
            sa.Column("normalized_invoice_number", sa.String(length=80), nullable=True)
        )
        batch.add_column(sa.Column("invoice_date", sa.Date(), nullable=True))
        batch.add_column(sa.Column("subtotal_minor", sa.BigInteger(), nullable=True))
        batch.add_column(sa.Column("discount_minor", sa.BigInteger(), nullable=True))
        batch.add_column(sa.Column("tax_minor", sa.BigInteger(), nullable=True))
        batch.add_column(sa.Column("tax_mode", sa.String(length=16), nullable=True))
        batch.add_column(
            sa.Column(
                "financial_state", sa.String(length=16), server_default="legacy", nullable=False
            )
        )
        batch.add_column(sa.Column("validated_extraction_id", sa.String(length=32), nullable=True))
        batch.create_unique_constraint("uq_invoices_organization_id_id", ["organization_id", "id"])
        batch.create_unique_constraint(
            "uq_invoices_organization_id_id_currency", ["organization_id", "id", "currency"]
        )
        batch.create_unique_constraint(
            "uq_invoices_organization_id_id_purchase_order_id",
            ["organization_id", "id", "purchase_order_id"],
        )
        batch.create_unique_constraint(
            "uq_invoices_organization_id_id_vendor_id",
            ["organization_id", "id", "vendor_id"],
        )
        batch.create_unique_constraint(
            "uq_invoices_organization_id_vendor_id_normalized_invoice_number",
            ["organization_id", "vendor_id", "normalized_invoice_number"],
        )
        batch.create_check_constraint(
            "ck_invoices_subtotal_minor",
            "subtotal_minor >= 0 AND subtotal_minor <= 9007199254740991",
        )
        batch.create_check_constraint(
            "ck_invoices_discount_minor",
            "discount_minor >= 0 AND discount_minor <= 9007199254740991",
        )
        batch.create_check_constraint(
            "ck_invoices_tax_minor", "tax_minor >= 0 AND tax_minor <= 9007199254740991"
        )
        batch.create_check_constraint(
            "ck_invoices_financial_state", "financial_state IN ('legacy','draft','validated')"
        )
        batch.create_check_constraint(
            "ck_invoices_normalized_number",
            "normalized_invoice_number = upper(replace(trim(supplier_invoice_number), ' ', ''))",
        )
        batch.create_check_constraint(
            "ck_invoices_currency", "currency IN ('USD','EUR','PHP','JPY','KWD')"
        )
        batch.create_check_constraint(
            "ck_invoices_validated_header",
            (
                "financial_state != 'validated' OR (vendor_id IS NOT NULL AND "
                "supplier_invoice_number IS NOT NULL AND normalized_invoice_number IS NOT NULL AND "
                "length(normalized_invoice_number) > 0 AND currency IS NOT NULL AND subtotal_minor "
                "IS NOT NULL AND discount_minor IS NOT NULL AND tax_minor IS NOT NULL AND "
                "validated_extraction_id IS NOT NULL AND amount_minor IS NOT NULL AND tax_mode IS "
                "NOT NULL AND tax_mode IN ('exclusive','inclusive') AND discount_minor <= "
                "subtotal_minor AND amount_minor = subtotal_minor - discount_minor + CASE WHEN "
                "tax_mode = 'exclusive' THEN tax_minor ELSE 0 END)"
            ),
        )
        if sqlite:
            batch.create_foreign_key(
                "fk_invoices_vendor",
                "vendors",
                ["organization_id", "vendor_id"],
                ["organization_id", "id"],
            )
            batch.create_foreign_key(
                "fk_invoices_organization", "organizations", ["organization_id"], ["id"]
            )
            batch.create_foreign_key(
                "fk_invoices_po_currency",
                "purchase_orders",
                ["organization_id", "purchase_order_id", "currency"],
                ["organization_id", "id", "currency"],
            )
            batch.create_foreign_key(
                "fk_invoices_validated_extraction",
                "extraction_versions",
                ["organization_id", "validated_extraction_id", "id"],
                ["organization_id", "id", "invoice_id"],
                use_alter=True,
            )
            batch.create_foreign_key(
                "fk_invoices_po_tenant",
                "purchase_orders",
                ["organization_id", "purchase_order_id"],
                ["organization_id", "id"],
            )
            batch.create_foreign_key(
                "fk_invoices_po_vendor",
                "purchase_orders",
                ["organization_id", "purchase_order_id", "vendor_id"],
                ["organization_id", "id", "vendor_id"],
            )
    with op.batch_alter_table(
        "job_execution_records",
        naming_convention={("pk"): "pk_%(table_name)s"},
        reflect_kwargs={("resolve_fks"): False},
    ) as batch:
        batch.add_column(
            sa.Column(
                "organization_id", sa.String(length=32), server_default="demo", nullable=False
            )
        )
        batch.add_column(sa.Column("job_id", sa.String(length=32), nullable=True))
        if sqlite:
            batch.drop_constraint("pk_job_execution_records", type_="primary")
        else:
            batch.drop_constraint(
                sa.inspect(op.get_bind()).get_pk_constraint("job_execution_records")["name"],
                type_="primary",
            )
        batch.create_primary_key("pk_job_execution_records", ["organization_id", "key"])
        if sqlite:
            batch.create_foreign_key(
                "fk_job_execution_records_job_tenant",
                "jobs",
                ["organization_id", "job_id"],
                ["organization_id", "id"],
            )
            batch.create_foreign_key(
                "fk_job_execution_records_organization",
                "organizations",
                ["organization_id"],
                ["id"],
            )
    with op.batch_alter_table(
        "jobs",
        naming_convention={("pk"): "pk_%(table_name)s"},
        reflect_kwargs={("resolve_fks"): False},
    ) as batch:
        batch.add_column(
            sa.Column("max_attempts", sa.Integer(), server_default="5", nullable=False)
        )
        batch.add_column(sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("lease_owner", sa.String(length=80), nullable=True))
        batch.add_column(sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("trace_id", sa.String(length=64), nullable=True))
        batch.create_index(
            "ix_jobs_recovery",
            ["organization_id", "state", "next_attempt_at", "lease_expires_at"],
            unique=False,
        )
        batch.create_unique_constraint("uq_jobs_organization_id_id", ["organization_id", "id"])
        batch.create_check_constraint(
            "ck_jobs_attempts",
            "max_attempts BETWEEN 1 AND 5 AND attempts >= 0 AND attempts <= max_attempts",
        )
        if sqlite:
            batch.create_foreign_key(
                "fk_jobs_invoice_tenant",
                "invoices",
                ["organization_id", "invoice_id"],
                ["organization_id", "id"],
            )
            batch.create_foreign_key(
                "fk_jobs_replay_tenant",
                "jobs",
                ["organization_id", "replay_of"],
                ["organization_id", "id"],
            )
    with op.batch_alter_table(
        "outbox_events",
        naming_convention={("pk"): "pk_%(table_name)s"},
        reflect_kwargs={("resolve_fks"): False},
    ) as batch:
        batch.add_column(
            sa.Column(
                "organization_id", sa.String(length=32), server_default="demo", nullable=False
            )
        )
        batch.add_column(sa.Column("attempts", sa.Integer(), server_default="0", nullable=False))
        batch.add_column(sa.Column("available_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("lease_owner", sa.String(length=80), nullable=True))
        batch.add_column(sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True))
        batch.create_index(
            "ix_outbox_dispatch",
            ["published_at", "available_at", "lease_expires_at"],
            unique=False,
        )
        if sqlite:
            batch.create_foreign_key(
                "fk_outbox_events_organization",
                "organizations",
                ["organization_id"],
                ["id"],
            )
            batch.create_foreign_key(
                "fk_outbox_events_job_tenant",
                "jobs",
                ["organization_id", "job_id"],
                ["organization_id", "id"],
            )
        batch.create_check_constraint("ck_outbox_events_attempts", "attempts >= 0")
    with op.batch_alter_table(
        "purchase_orders",
        table_args=(sa.CheckConstraint("amount_minor >= 0 AND amount_minor <= 9007199254740991"),),
        naming_convention={("pk"): "pk_%(table_name)s"},
        reflect_kwargs={("resolve_fks"): False},
    ) as batch:
        batch.add_column(
            sa.Column("tax_mode", sa.String(16), nullable=False, server_default="exclusive")
        )
        batch.add_column(sa.Column("vendor_id", sa.String(length=32), nullable=True))
        batch.add_column(
            sa.Column("lines_normalized", sa.Boolean(), server_default="false", nullable=False)
        )
        batch.create_unique_constraint(
            "uq_purchase_orders_organization_id_id", ["organization_id", "id"]
        )
        batch.create_unique_constraint(
            "uq_purchase_orders_organization_id_id_currency",
            ["organization_id", "id", "currency"],
        )
        batch.create_unique_constraint(
            "uq_purchase_orders_organization_id_id_vendor_id",
            ["organization_id", "id", "vendor_id"],
        )
        batch.create_check_constraint(
            "ck_purchase_orders_tax_minor", "tax_minor >= 0 AND tax_minor <= 9007199254740991"
        )
        batch.create_check_constraint(
            "ck_purchase_orders_subtotal_minor",
            "subtotal_minor >= 0 AND subtotal_minor <= 9007199254740991",
        )
        batch.create_check_constraint(
            "ck_purchase_orders_discount_minor",
            "discount_minor >= 0 AND discount_minor <= 9007199254740991",
        )
        batch.create_check_constraint(
            "ck_purchase_orders_currency", "currency IN ('USD','EUR','PHP','JPY','KWD')"
        )
        if sqlite:
            batch.create_foreign_key(
                "fk_purchase_orders_vendor",
                "vendors",
                ["organization_id", "vendor_id"],
                ["organization_id", "id"],
            )
            batch.create_foreign_key(
                "fk_purchase_orders_organization",
                "organizations",
                ["organization_id"],
                ["id"],
            )
        batch.create_check_constraint(
            "ck_purchase_orders_tax_mode", "tax_mode IN ('exclusive','inclusive')"
        )
    with op.batch_alter_table(
        "reconciliation_runs",
        naming_convention={("pk"): "pk_%(table_name)s"},
        reflect_kwargs={("resolve_fks"): False},
    ) as batch:
        batch.add_column(sa.Column("extraction_version_id", sa.String(length=32), nullable=True))
        batch.add_column(sa.Column("tolerance_policy_id", sa.String(length=32), nullable=True))
        batch.create_unique_constraint(
            "uq_reconciliation_runs_organization_id_id", ["organization_id", "id"]
        )
        batch.create_unique_constraint(
            "uq_reconciliation_runs_organization_id_id_invoice_id",
            ["organization_id", "id", "invoice_id"],
        )
        if sqlite:
            batch.create_foreign_key(
                "fk_reconciliation_runs_invoice_tenant",
                "invoices",
                ["organization_id", "invoice_id"],
                ["organization_id", "id"],
            )
            batch.create_foreign_key(
                "fk_reconciliation_runs_extraction_invoice",
                "extraction_versions",
                ["organization_id", "extraction_version_id", "invoice_id"],
                ["organization_id", "id", "invoice_id"],
            )
            batch.create_foreign_key(
                "fk_reconciliation_runs_job_tenant",
                "jobs",
                ["organization_id", "job_id"],
                ["organization_id", "id"],
            )
            batch.create_foreign_key(
                "fk_reconciliation_runs_tolerance",
                "tolerance_policies",
                ["organization_id", "tolerance_policy_id"],
                ["organization_id", "id"],
            )
    with op.batch_alter_table(
        "review_decisions",
        naming_convention={("pk"): "pk_%(table_name)s"},
        reflect_kwargs={("resolve_fks"): False},
    ) as batch:
        batch.add_column(sa.Column("user_id", sa.String(length=32), nullable=True))
        batch.create_unique_constraint(
            "uq_review_decisions_organization_id_id", ["organization_id", "id"]
        )
        if sqlite:
            batch.create_foreign_key(
                "fk_review_decisions_reviewer",
                "organization_memberships",
                ["organization_id", "user_id"],
                ["organization_id", "user_id"],
            )
            batch.create_foreign_key(
                "fk_review_decisions_run_invoice",
                "reconciliation_runs",
                ["organization_id", "reconciliation_id", "invoice_id"],
                ["organization_id", "id", "invoice_id"],
            )
            batch.create_foreign_key(
                "fk_review_decisions_invoice_tenant",
                "invoices",
                ["organization_id", "invoice_id"],
                ["organization_id", "id"],
            )
    if sqlite:
        with op.batch_alter_table(
            "webhook_receipts", reflect_kwargs={("resolve_fks"): False}
        ) as batch:
            batch.create_foreign_key(
                "fk_webhook_receipts_job_tenant",
                "jobs",
                ["organization_id", "job_id"],
                ["organization_id", "id"],
            )
    op.create_table(
        "purchase_order_lines",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("organization_id", sa.String(length=32), nullable=False),
        sa.Column("purchase_order_id", sa.String(length=32), nullable=False),
        sa.Column("reference", sa.String(length=32), nullable=False),
        sa.Column("description", sa.String(length=200), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column(
            "quantity", sa.Numeric(15, 6).with_variant(sa.String(32), "sqlite"), nullable=False
        ),
        sa.Column("unit_price_minor", sa.BigInteger(), nullable=False),
        sa.Column("line_total_minor", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "currency IN ('USD','EUR','PHP','JPY','KWD')",
            name="ck_purchase_order_lines_currency",
        ),
        sa.CheckConstraint(
            "line_total_minor = riq_line_minor(quantity, unit_price_minor)",
            name="ck_purchase_order_lines_line_total",
        ),
        sa.CheckConstraint(
            "line_total_minor >= 0 AND line_total_minor <= 9007199254740991",
            name="ck_purchase_order_lines_line_total_minor",
        ),
        sa.CheckConstraint(
            "riq_quantity_valid(quantity) = 1", name="ck_purchase_order_lines_quantity"
        ),
        sa.CheckConstraint(
            "unit_price_minor >= 0 AND unit_price_minor <= 9007199254740991",
            name="ck_purchase_order_lines_unit_price_minor",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "purchase_order_id", "currency"],
            [
                "purchase_orders.organization_id",
                "purchase_orders.id",
                "purchase_orders.currency",
            ],
            name="fk_purchase_order_lines_po_currency",
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id", "id", name="uq_purchase_order_lines_organization_id_id"
        ),
        sa.UniqueConstraint(
            "organization_id",
            "purchase_order_id",
            "id",
            name="uq_purchase_order_lines_organization_id_purchase_order_id_id",
        ),
        sa.UniqueConstraint(
            "organization_id",
            "purchase_order_id",
            "reference",
            name="uq_purchase_order_lines_organization_id_purchase_order_id_refer",
        ),
    )
    op.create_index(
        op.f("ix_purchase_order_lines_organization_id"),
        "purchase_order_lines",
        ["organization_id"],
        unique=False,
    )
    op.create_table(
        "duplicate_invoice_attempts",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("organization_id", sa.String(length=32), nullable=False),
        sa.Column("vendor_id", sa.String(length=32), nullable=False),
        sa.Column("existing_invoice_id", sa.String(length=32), nullable=False),
        sa.Column("supplier_invoice_number", sa.String(length=80), nullable=False),
        sa.Column("document_sha256", sa.String(length=64), nullable=True),
        sa.Column("reason", sa.String(length=200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id", "existing_invoice_id", "vendor_id"],
            ["invoices.organization_id", "invoices.id", "invoices.vendor_id"],
            name="fk_duplicate_invoice_attempts_original_vendor",
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_duplicate_invoice_attempts_organization_id"),
        "duplicate_invoice_attempts",
        ["organization_id"],
        unique=False,
    )
    op.create_table(
        "extraction_versions",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("organization_id", sa.String(length=32), nullable=False),
        sa.Column("invoice_id", sa.String(length=32), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("document_sha256", sa.String(length=64), nullable=True),
        sa.Column("origin", sa.String(length=32), nullable=False),
        sa.Column("schema_version", sa.String(length=32), nullable=False),
        sa.Column("validation_state", sa.String(length=16), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "number >= 1 AND validation_state IN ('untrusted','validated','rejected')",
            name="ck_extraction_versions_version",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "invoice_id"],
            ["invoices.organization_id", "invoices.id"],
            name="fk_extraction_versions_invoice",
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id",
            "id",
            "invoice_id",
            name="uq_extraction_versions_organization_id_id_invoice_id",
        ),
        sa.UniqueConstraint(
            "organization_id", "id", name="uq_extraction_versions_organization_id_id"
        ),
        sa.UniqueConstraint(
            "organization_id",
            "invoice_id",
            "number",
            name="uq_extraction_versions_organization_id_invoice_id_number",
        ),
    )
    op.create_index(
        op.f("ix_extraction_versions_organization_id"),
        "extraction_versions",
        ["organization_id"],
        unique=False,
    )
    op.create_table(
        "goods_receipt_lines",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("organization_id", sa.String(length=32), nullable=False),
        sa.Column("goods_receipt_id", sa.String(length=32), nullable=False),
        sa.Column("purchase_order_id", sa.String(length=32), nullable=False),
        sa.Column("purchase_order_line_id", sa.String(length=32), nullable=False),
        sa.Column(
            "quantity", sa.Numeric(15, 6).with_variant(sa.String(32), "sqlite"), nullable=False
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "riq_quantity_valid(quantity) = 1", name="ck_goods_receipt_lines_quantity"
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "goods_receipt_id", "purchase_order_id"],
            [
                "goods_receipts.organization_id",
                "goods_receipts.id",
                "goods_receipts.purchase_order_id",
            ],
            name="fk_goods_receipt_lines_receipt_po",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "purchase_order_id", "purchase_order_line_id"],
            [
                "purchase_order_lines.organization_id",
                "purchase_order_lines.purchase_order_id",
                "purchase_order_lines.id",
            ],
            name="fk_goods_receipt_lines_po_line",
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id",
            "goods_receipt_id",
            "purchase_order_line_id",
            name="uq_goods_receipt_lines_organization_id_goods_receipt_id_purchas",
        ),
        sa.UniqueConstraint(
            "organization_id", "id", name="uq_goods_receipt_lines_organization_id_id"
        ),
    )
    op.create_index(
        op.f("ix_goods_receipt_lines_organization_id"),
        "goods_receipt_lines",
        ["organization_id"],
        unique=False,
    )
    op.create_table(
        "extracted_fields",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("organization_id", sa.String(length=32), nullable=False),
        sa.Column("extraction_version_id", sa.String(length=32), nullable=False),
        sa.Column("invoice_id", sa.String(length=32), nullable=False),
        sa.Column("path", sa.String(length=160), nullable=False),
        sa.Column("value", sa.JSON(), nullable=False),
        sa.Column("page", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column(
            "confidence", sa.Numeric(15, 6).with_variant(sa.String(32), "sqlite"), nullable=True
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("page >= 1", name="ck_extracted_fields_page"),
        sa.ForeignKeyConstraint(
            ["organization_id", "extraction_version_id", "invoice_id"],
            [
                "extraction_versions.organization_id",
                "extraction_versions.id",
                "extraction_versions.invoice_id",
            ],
            name="fk_extracted_fields_extraction_invoice",
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id",
            "extraction_version_id",
            "path",
            "page",
            name="uq_extracted_fields_organization_id_extraction_version_id_path_",
        ),
        sa.CheckConstraint(
            "confidence IS NULL OR riq_rate_valid(confidence) = 1",
            name="ck_extracted_fields_confidence",
        ),
    )
    op.create_index(
        op.f("ix_extracted_fields_organization_id"),
        "extracted_fields",
        ["organization_id"],
        unique=False,
    )
    op.create_table(
        "failed_jobs",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("organization_id", sa.String(length=32), nullable=False),
        sa.Column("job_id", sa.String(length=32), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=False),
        sa.Column("retryable", sa.Boolean(), nullable=False),
        sa.Column("next_action", sa.String(length=200), nullable=False),
        sa.Column("trace_id", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id", "job_id"],
            ["jobs.organization_id", "jobs.id"],
            name="fk_failed_jobs_job_tenant",
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id", "job_id", name="uq_failed_jobs_organization_id_job_id"
        ),
    )
    op.create_index(
        op.f("ix_failed_jobs_organization_id"),
        "failed_jobs",
        ["organization_id"],
        unique=False,
    )
    op.create_table(
        "invoice_lines",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("organization_id", sa.String(length=32), nullable=False),
        sa.Column("invoice_id", sa.String(length=32), nullable=False),
        sa.Column("extraction_version_id", sa.String(length=32), nullable=False),
        sa.Column("line_number", sa.Integer(), nullable=False),
        sa.Column("purchase_order_id", sa.String(length=32), nullable=True),
        sa.Column("purchase_order_line_id", sa.String(length=32), nullable=True),
        sa.Column("description", sa.String(length=200), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column(
            "quantity", sa.Numeric(15, 6).with_variant(sa.String(32), "sqlite"), nullable=False
        ),
        sa.Column("unit_price_minor", sa.BigInteger(), nullable=False),
        sa.Column("line_total_minor", sa.BigInteger(), nullable=False),
        sa.Column("discount_minor", sa.BigInteger(), nullable=False),
        sa.Column(
            "tax_rate", sa.Numeric(15, 6).with_variant(sa.String(32), "sqlite"), nullable=False
        ),
        sa.Column("tax_mode", sa.String(length=16), nullable=False),
        sa.Column("tax_minor", sa.BigInteger(), nullable=False),
        sa.Column("total_minor", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "currency IN ('USD','EUR','PHP','JPY','KWD')", name="ck_invoice_lines_currency"
        ),
        sa.CheckConstraint(
            "tax_mode IN ('exclusive','inclusive') AND discount_minor <= line_total_minor",
            name="ck_invoice_lines_mode",
        ),
        sa.CheckConstraint(
            (
                "total_minor = line_total_minor - discount_minor + "
                "CASE WHEN tax_mode = 'exclusive' THEN tax_minor "
                "ELSE 0 END"
            ),
            name="ck_invoice_lines_total",
        ),
        sa.CheckConstraint(
            "discount_minor >= 0 AND discount_minor <= 9007199254740991",
            name="ck_invoice_lines_discount_minor",
        ),
        sa.CheckConstraint(
            "line_total_minor = riq_line_minor(quantity, unit_price_minor)",
            name="ck_invoice_lines_line_total",
        ),
        sa.CheckConstraint(
            "line_total_minor >= 0 AND line_total_minor <= 9007199254740991",
            name="ck_invoice_lines_line_total_minor",
        ),
        sa.CheckConstraint("riq_quantity_valid(quantity) = 1", name="ck_invoice_lines_quantity"),
        sa.CheckConstraint("riq_rate_valid(tax_rate) = 1", name="ck_invoice_lines_rate"),
        sa.CheckConstraint(
            "tax_minor = riq_tax_minor(line_total_minor, discount_minor, tax_rate, tax_mode)",
            name="ck_invoice_lines_tax",
        ),
        sa.CheckConstraint(
            "tax_minor >= 0 AND tax_minor <= 9007199254740991",
            name="ck_invoice_lines_tax_minor",
        ),
        sa.CheckConstraint(
            "total_minor >= 0 AND total_minor <= 9007199254740991",
            name="ck_invoice_lines_total_minor",
        ),
        sa.CheckConstraint(
            "unit_price_minor >= 0 AND unit_price_minor <= 9007199254740991",
            name="ck_invoice_lines_unit_price_minor",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "extraction_version_id", "invoice_id"],
            [
                "extraction_versions.organization_id",
                "extraction_versions.id",
                "extraction_versions.invoice_id",
            ],
            name="fk_invoice_lines_extraction_invoice",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "invoice_id", "currency"],
            ["invoices.organization_id", "invoices.id", "invoices.currency"],
            name="fk_invoice_lines_invoice_currency",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "invoice_id", "purchase_order_id"],
            ["invoices.organization_id", "invoices.id", "invoices.purchase_order_id"],
            name="fk_invoice_lines_invoice_po",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "purchase_order_id", "purchase_order_line_id"],
            [
                "purchase_order_lines.organization_id",
                "purchase_order_lines.purchase_order_id",
                "purchase_order_lines.id",
            ],
            name="fk_invoice_lines_po_line",
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id",
            "extraction_version_id",
            "line_number",
            name="uq_invoice_lines_organization_id_extraction_version_id_line_num",
        ),
        sa.UniqueConstraint(
            "organization_id",
            "id",
            "invoice_id",
            name="uq_invoice_lines_organization_id_id_invoice_id",
        ),
        sa.UniqueConstraint("organization_id", "id", name="uq_invoice_lines_organization_id_id"),
        sa.CheckConstraint("line_number >= 1", name="ck_invoice_lines_line_number"),
    )
    op.create_index(
        op.f("ix_invoice_lines_organization_id"),
        "invoice_lines",
        ["organization_id"],
        unique=False,
    )
    op.create_table(
        "job_attempts",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("organization_id", sa.String(length=32), nullable=False),
        sa.Column("job_id", sa.String(length=32), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("worker", sa.String(length=80), nullable=False),
        sa.Column("trace_id", sa.String(length=64), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("retryable", sa.Boolean(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "number BETWEEN 1 AND 5 AND state IN ('running','succeeded','failed')",
            name="ck_job_attempts_attempt",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "job_id"],
            ["jobs.organization_id", "jobs.id"],
            name="fk_job_attempts_job_tenant",
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id",
            "job_id",
            "number",
            name="uq_job_attempts_organization_id_job_id_number",
        ),
        sa.CheckConstraint(
            (
                "(state = 'running' AND finished_at IS NULL) OR "
                "(state IN ('failed','succeeded') AND finished_at "
                "IS NOT NULL AND finished_at >= started_at)"
            ),
            name="ck_job_attempts_finished",
        ),
    )
    op.create_index(
        op.f("ix_job_attempts_organization_id"),
        "job_attempts",
        ["organization_id"],
        unique=False,
    )
    op.create_table(
        "reconciliation_line_results",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("organization_id", sa.String(length=32), nullable=False),
        sa.Column("reconciliation_id", sa.String(length=32), nullable=False),
        sa.Column("invoice_id", sa.String(length=32), nullable=False),
        sa.Column("invoice_line_id", sa.String(length=32), nullable=False),
        sa.Column("outcome", sa.String(length=32), nullable=False),
        sa.Column("amount_difference_minor", sa.BigInteger(), nullable=False),
        sa.Column(
            "quantity_difference",
            sa.Numeric(15, 6).with_variant(sa.String(32), "sqlite"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id", "invoice_line_id", "invoice_id"],
            ["invoice_lines.organization_id", "invoice_lines.id", "invoice_lines.invoice_id"],
            name="fk_reconciliation_line_results_invoice_line",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "reconciliation_id", "invoice_id"],
            [
                "reconciliation_runs.organization_id",
                "reconciliation_runs.id",
                "reconciliation_runs.invoice_id",
            ],
            name="fk_reconciliation_line_results_run_invoice",
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "amount_difference_minor BETWEEN -9007199254740991 AND 9007199254740991",
            name="ck_reconciliation_line_results_amount",
        ),
        sa.UniqueConstraint(
            "organization_id",
            "reconciliation_id",
            "invoice_line_id",
            name="uq_reconciliation_line_results_organization_id_reconciliation_i",
        ),
    )
    op.create_index(
        op.f("ix_reconciliation_line_results_organization_id"),
        "reconciliation_line_results",
        ["organization_id"],
        unique=False,
    )
    op.create_index(
        "uq_job_attempts_active",
        "job_attempts",
        ["organization_id", "job_id"],
        unique=True,
        postgresql_where=sa.text("state='running'"),
        sqlite_where=sa.text("state='running'"),
    )
    _backfill()
    if not sqlite:
        op.create_foreign_key(
            "fk_api_credentials_membership",
            "api_credentials",
            "organization_memberships",
            ["organization_id", "user_id", "role"],
            ["organization_id", "user_id", "role"],
        )
        op.create_foreign_key(
            "fk_audit_events_organization",
            "audit_events",
            "organizations",
            ["organization_id"],
            ["id"],
        )
        op.create_foreign_key(
            "fk_discrepancies_invoice_tenant",
            "discrepancies",
            "invoices",
            ["organization_id", "invoice_id"],
            ["organization_id", "id"],
        )
        op.create_foreign_key(
            "fk_discrepancies_run_invoice",
            "discrepancies",
            "reconciliation_runs",
            ["organization_id", "reconciliation_id", "invoice_id"],
            ["organization_id", "id", "invoice_id"],
        )
        op.create_foreign_key(
            "fk_goods_receipts_po_tenant",
            "goods_receipts",
            "purchase_orders",
            ["organization_id", "purchase_order_id"],
            ["organization_id", "id"],
        )
        op.create_foreign_key(
            "fk_invoices_vendor",
            "invoices",
            "vendors",
            ["organization_id", "vendor_id"],
            ["organization_id", "id"],
        )
        op.create_foreign_key(
            "fk_invoices_organization",
            "invoices",
            "organizations",
            ["organization_id"],
            ["id"],
        )
        op.create_foreign_key(
            "fk_invoices_po_currency",
            "invoices",
            "purchase_orders",
            ["organization_id", "purchase_order_id", "currency"],
            ["organization_id", "id", "currency"],
        )
        op.create_foreign_key(
            "fk_invoices_validated_extraction",
            "invoices",
            "extraction_versions",
            ["organization_id", "validated_extraction_id", "id"],
            ["organization_id", "id", "invoice_id"],
            use_alter=True,
        )
        op.create_foreign_key(
            "fk_invoices_po_tenant",
            "invoices",
            "purchase_orders",
            ["organization_id", "purchase_order_id"],
            ["organization_id", "id"],
        )
        op.create_foreign_key(
            "fk_invoices_po_vendor",
            "invoices",
            "purchase_orders",
            ["organization_id", "purchase_order_id", "vendor_id"],
            ["organization_id", "id", "vendor_id"],
        )
        op.create_foreign_key(
            "fk_job_execution_records_job_tenant",
            "job_execution_records",
            "jobs",
            ["organization_id", "job_id"],
            ["organization_id", "id"],
        )
        op.create_foreign_key(
            "fk_outbox_events_organization",
            "job_execution_records",
            "organizations",
            ["organization_id"],
            ["id"],
        )
        op.create_foreign_key(
            "fk_jobs_invoice_tenant",
            "jobs",
            "invoices",
            ["organization_id", "invoice_id"],
            ["organization_id", "id"],
        )
        op.create_foreign_key(
            "fk_jobs_replay_tenant",
            "jobs",
            "jobs",
            ["organization_id", "replay_of"],
            ["organization_id", "id"],
        )
        op.create_foreign_key(
            "fk_outbox_events_organization",
            "outbox_events",
            "organizations",
            ["organization_id"],
            ["id"],
        )
        op.create_foreign_key(
            "fk_outbox_events_job_tenant",
            "outbox_events",
            "jobs",
            ["organization_id", "job_id"],
            ["organization_id", "id"],
        )
        op.create_foreign_key(
            "fk_purchase_orders_vendor",
            "purchase_orders",
            "vendors",
            ["organization_id", "vendor_id"],
            ["organization_id", "id"],
        )
        op.create_foreign_key(
            "fk_purchase_orders_organization",
            "purchase_orders",
            "organizations",
            ["organization_id"],
            ["id"],
        )
        op.create_foreign_key(
            "fk_reconciliation_runs_invoice_tenant",
            "reconciliation_runs",
            "invoices",
            ["organization_id", "invoice_id"],
            ["organization_id", "id"],
        )
        op.create_foreign_key(
            "fk_reconciliation_runs_extraction_invoice",
            "reconciliation_runs",
            "extraction_versions",
            ["organization_id", "extraction_version_id", "invoice_id"],
            ["organization_id", "id", "invoice_id"],
        )
        op.create_foreign_key(
            "fk_reconciliation_runs_job_tenant",
            "reconciliation_runs",
            "jobs",
            ["organization_id", "job_id"],
            ["organization_id", "id"],
        )
        op.create_foreign_key(
            "fk_reconciliation_runs_tolerance",
            "reconciliation_runs",
            "tolerance_policies",
            ["organization_id", "tolerance_policy_id"],
            ["organization_id", "id"],
        )
        op.create_foreign_key(
            "fk_review_decisions_reviewer",
            "review_decisions",
            "organization_memberships",
            ["organization_id", "user_id"],
            ["organization_id", "user_id"],
        )
        op.create_foreign_key(
            "fk_review_decisions_run_invoice",
            "review_decisions",
            "reconciliation_runs",
            ["organization_id", "reconciliation_id", "invoice_id"],
            ["organization_id", "id", "invoice_id"],
        )
        op.create_foreign_key(
            "fk_review_decisions_invoice_tenant",
            "review_decisions",
            "invoices",
            ["organization_id", "invoice_id"],
            ["organization_id", "id"],
        )
        op.create_foreign_key(
            "fk_webhook_receipts_job_tenant",
            "webhook_receipts",
            "jobs",
            ["organization_id", "job_id"],
            ["organization_id", "id"],
        )
    _triggers()
    _attempt_guards()


def downgrade() -> None:
    raise RuntimeError(
        "Financial records and audit history require a forward migration; destructive "
        "downgrade refused."
    )


def _arithmetic() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(
        "CREATE FUNCTION riq_quantity_valid(q numeric) RETURNS integer\n        LANGUAGE SQL "
        "IMMUTABLE STRICT AS $$ SELECT CASE WHEN q > 0\n        AND q <= 999999999.999999 AND "
        "round(q,6)=q THEN 1 ELSE 0 END $$"
    )
    op.execute(
        "CREATE FUNCTION riq_rate_valid(r numeric) RETURNS integer\n        LANGUAGE SQL "
        "IMMUTABLE STRICT AS $$ SELECT CASE WHEN r >= 0\n        AND r <= 1 AND round(r,6)=r "
        "THEN 1 ELSE 0 END $$"
    )
    op.execute(
        "CREATE FUNCTION riq_line_minor(q numeric, p bigint) RETURNS bigint\n        LANGUAGE "
        "SQL IMMUTABLE STRICT AS $$ SELECT round(q*p,0)::bigint $$"
    )
    op.execute(
        "CREATE FUNCTION riq_tax_minor(g bigint, d bigint, r numeric, m text)\n        RETURNS "
        "bigint LANGUAGE SQL IMMUTABLE STRICT AS $$ SELECT round(\n        CASE WHEN "
        "m='exclusive' THEN (g-d)*r ELSE (g-d)*r/(1+r) END,0)::bigint $$"
    )


def _backfill() -> None:
    bind = op.get_bind()
    metadata = sa.MetaData()
    metadata.reflect(bind=bind)
    now = datetime.now(UTC)
    sqlite = bind.dialect.name == "sqlite"

    def ident(label: str) -> str:
        return uuid5(NAMESPACE_URL, "reconcileiq:" + label).hex

    def audit(org: str, entity: str, action: str) -> None:
        bind.execute(
            metadata.tables["audit_events"]
            .insert()
            .values(
                id=ident("migration:" + org + ":" + entity + ":" + action),
                organization_id=org,
                entity_id=entity,
                actor="migration:0003",
                action=action,
                detail="Existing record preserved; normalized relationship backfilled.",
                occurred_at=now,
            )
        )

    vendors = {}
    for name in ("purchase_orders", "invoices"):
        table = metadata.tables[name]
        for row in bind.execute(sa.select(table)).mappings().all():
            if row["vendor"] is None:
                continue
            org, normalized = (row["organization_id"], row["vendor"].strip().casefold())
            key = (org, normalized)
            if key not in vendors:
                vendor_id = ident(f"vendor:{org}:{normalized}")
                bind.execute(
                    metadata.tables["vendors"]
                    .insert()
                    .values(
                        id=vendor_id,
                        organization_id=org,
                        code="legacy-" + vendor_id[:12],
                        name=row["vendor"],
                        normalized_name=normalized,
                        default_currency=row["currency"],
                        active=True,
                        created_at=now,
                    )
                )
                vendors[key] = vendor_id
                audit(org, vendor_id, "vendor_backfilled")
            bind.execute(
                table.update().where(table.c.id == row["id"]).values(vendor_id=vendors[key])
            )
            audit(org, row["id"], "vendor_relationship_backfilled")
    for row in bind.execute(sa.select(metadata.tables["api_credentials"])).mappings().all():
        org, actor = (row["organization_id"], row["actor"])
        user_id = ident(f"user:{org}:{actor}")
        users = metadata.tables["users"]
        if bind.scalar(sa.select(users.c.id).where(users.c.id == user_id)) is None:
            bind.execute(
                users.insert().values(
                    id=user_id,
                    subject=f"local:{org}:{actor}",
                    display_name=actor,
                    active=True,
                    created_at=now,
                )
            )
            bind.execute(
                metadata.tables["organization_memberships"]
                .insert()
                .values(
                    organization_id=org,
                    user_id=user_id,
                    role=row["role"],
                    active=True,
                    created_at=now,
                )
            )
            audit(org, user_id, "membership_backfilled")
        bind.execute(
            metadata.tables["api_credentials"]
            .update()
            .where(metadata.tables["api_credentials"].c.digest == row["digest"])
            .values(user_id=user_id)
        )
    lines = metadata.tables["purchase_order_lines"]
    for row in bind.execute(sa.select(metadata.tables["purchase_orders"])).mappings().all():
        for line in row["line_items"]:
            quantity = str(line["quantity"]) if sqlite else Decimal(line["quantity"])
            bind.execute(
                lines.insert().values(
                    id=ident(f"po-line:{row['id']}:{line['id']}"),
                    organization_id=row["organization_id"],
                    purchase_order_id=row["id"],
                    reference=line["id"],
                    description=line["description"],
                    currency=row["currency"],
                    quantity=quantity,
                    unit_price_minor=line["unit_price_minor"],
                    line_total_minor=line["line_total_minor"],
                    created_at=row["created_at"],
                )
            )
        if row["line_items"]:
            bind.execute(
                metadata.tables["purchase_orders"]
                .update()
                .where(metadata.tables["purchase_orders"].c.id == row["id"])
                .values(lines_normalized=True)
            )
            audit(row["organization_id"], row["id"], "purchase_order_lines_backfilled")
    for row in bind.execute(sa.select(metadata.tables["goods_receipts"])).mappings().all():
        for line in row["lines"]:
            bind.execute(
                metadata.tables["goods_receipt_lines"]
                .insert()
                .values(
                    id=ident(f"receipt-line:{row['id']}:{line['purchase_order_line_id']}"),
                    organization_id=row["organization_id"],
                    goods_receipt_id=row["id"],
                    purchase_order_id=row["purchase_order_id"],
                    purchase_order_line_id=ident(
                        f"po-line:{row['purchase_order_id']}:{line['purchase_order_line_id']}"
                    ),
                    quantity=str(line["quantity"]) if sqlite else Decimal(line["quantity"]),
                    created_at=row["created_at"],
                )
            )
        bind.execute(
            metadata.tables["goods_receipts"]
            .update()
            .where(metadata.tables["goods_receipts"].c.id == row["id"])
            .values(lines_normalized=True)
        )
        audit(row["organization_id"], row["id"], "receipt_lines_backfilled")
    op.execute(
        "UPDATE outbox_events SET organization_id =\n        (SELECT organization_id FROM jobs "
        "WHERE jobs.id = outbox_events.job_id)"
    )


def _guard(name: str, table: str, event: str, condition: str, message: str) -> None:
    if op.get_bind().dialect.name == "sqlite":
        op.execute(f"""CREATE TRIGGER {name} BEFORE {event} ON {table}
            WHEN {condition} BEGIN SELECT RAISE(ABORT, '{message}'); END""")
    else:
        op.execute(f"""CREATE FUNCTION {name}_fn() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN IF {condition} THEN RAISE EXCEPTION '{message}' USING ERRCODE='23514';
            END IF; RETURN NEW; END; $$""")
        level = "STATEMENT" if event == "TRUNCATE" else "ROW"
        op.execute(f"""CREATE TRIGGER {name} BEFORE {event} ON {table}
            FOR EACH {level} EXECUTE FUNCTION {name}_fn()""")


def _triggers() -> None:
    sqlite = op.get_bind().dialect.name == "sqlite"
    if sqlite:
        for event in ("UPDATE", "DELETE"):
            _guard(
                "audit_no_" + event.lower(),
                "audit_events",
                event,
                "1=1",
                "Audit events are append-only",
            )
    for table in (
        "extraction_versions",
        "extracted_fields",
        "invoice_lines",
        "goods_receipt_lines",
        "review_decisions",
        "duplicate_invoice_attempts",
        "reconciliation_line_results",
        "tolerance_policies",
        "failed_jobs",
        "job_execution_records",
    ):
        for event in ("UPDATE", "DELETE"):
            _guard(
                table + "_no_" + event.lower(),
                table,
                event,
                "1=1",
                "Financial history is immutable",
            )
        if not sqlite:
            _guard(
                table + "_no_truncate",
                table,
                "TRUNCATE",
                "1=1",
                "Financial history is immutable",
            )
    validated_bad = (
        "NEW.financial_state = 'validated' AND (\n        NOT EXISTS (SELECT 1 FROM "
        "extraction_versions e WHERE e.id=NEW.validated_extraction_id\n            AND "
        "e.organization_id=NEW.organization_id AND e.invoice_id=NEW.id\n            AND "
        "e.validation_state='validated')\n        OR NOT EXISTS (SELECT 1 FROM invoice_lines l\n "
        "           WHERE l.organization_id=NEW.organization_id AND l.invoice_id=NEW.id\n       "
        "     AND l.extraction_version_id=NEW.validated_extraction_id\n            HAVING "
        "count(*) > 0 AND sum(l.line_total_minor)=NEW.subtotal_minor\n            AND "
        "sum(l.discount_minor)=NEW.discount_minor AND sum(l.tax_minor)=NEW.tax_minor\n          "
        "  AND sum(l.total_minor)=NEW.amount_minor\n            AND sum(CASE WHEN "
        "l.tax_mode=NEW.tax_mode THEN 0 ELSE 1 END)=0))"
    )
    validated_bad = validated_bad.replace(
        "SELECT 1 FROM invoice_lines l", "SELECT count(*) FROM invoice_lines l"
    )
    for event in ("INSERT", "UPDATE"):
        _guard(
            "invoices_validate_" + event.lower(),
            "invoices",
            event,
            validated_bad,
            "Validated invoice totals must equal immutable extraction lines",
        )
    _guard(
        "invoices_no_unvalidate",
        "invoices",
        "UPDATE",
        "OLD.financial_state='validated' AND NEW.financial_state!='validated'",
        "Validated financial records cannot be downgraded",
    )
    _guard(
        "invoice_lines_no_append",
        "invoice_lines",
        "INSERT",
        (
            "EXISTS (\n        SELECT 1 FROM invoices i WHERE i.id=NEW.invoice_id AND "
            "i.organization_id=NEW.organization_id\n        AND i.financial_state='validated' AND "
            "i.validated_extraction_id=NEW.extraction_version_id)"
        ),
        "Validated extraction lines are sealed",
    )
    _guard(
        "extracted_fields_no_append",
        "extracted_fields",
        "INSERT",
        (
            "EXISTS (\n        SELECT 1 FROM invoices i WHERE i.id=NEW.invoice_id AND "
            "i.organization_id=NEW.organization_id\n        AND i.financial_state='validated' AND "
            "i.validated_extraction_id=NEW.extraction_version_id)"
        ),
        "Validated extraction provenance is sealed",
    )
    for event in ("INSERT", "UPDATE"):
        _guard(
            "purchase_orders_validate_" + event.lower(),
            "purchase_orders",
            event,
            (
                "NEW.lines_normalized AND (\n            "
                "NEW.subtotal_minor IS NULL OR NEW.tax_minor IS "
                "NULL OR NEW.discount_minor IS NULL\n            OR "
                "NEW.discount_minor > NEW.subtotal_minor\n          "
                "  OR NEW.amount_minor != NEW.subtotal_minor - "
                "NEW.discount_minor\n                + CASE WHEN "
                "NEW.tax_mode='exclusive' THEN NEW.tax_minor ELSE "
                "0 END\n            OR NOT EXISTS (SELECT count(*) "
                "FROM purchase_order_lines l WHERE "
                "l.purchase_order_id=NEW.id\n                AND "
                "l.organization_id=NEW.organization_id HAVING "
                "count(*) > 0\n                AND "
                "sum(l.line_total_minor)=NEW.subtotal_minor))"
            ),
            "Normalized purchase order totals must equal its lines",
        )
    for event in ("UPDATE", "DELETE"):
        _guard(
            "purchase_order_lines_no_" + event.lower(),
            "purchase_order_lines",
            event,
            "1=1",
            "Purchase order financial lines are immutable",
        )
    _guard(
        "purchase_order_lines_no_append",
        "purchase_order_lines",
        "INSERT",
        (
            "EXISTS (\n        SELECT 1 FROM purchase_orders p "
            "WHERE p.id=NEW.purchase_order_id\n        AND "
            "p.organization_id=NEW.organization_id AND "
            "p.lines_normalized)"
        ),
        "Purchase order lines are sealed",
    )
    _guard(
        "purchase_orders_no_unseal",
        "purchase_orders",
        "UPDATE",
        "OLD.lines_normalized AND NOT NEW.lines_normalized",
        "Purchase order lines cannot be unsealed",
    )
    _guard(
        "goods_receipts_no_unseal",
        "goods_receipts",
        "UPDATE",
        "OLD.lines_normalized AND NOT NEW.lines_normalized",
        "Receipt lines cannot be unsealed",
    )
    _guard(
        "goods_receipt_lines_no_append",
        "goods_receipt_lines",
        "INSERT",
        (
            "EXISTS (\n        SELECT 1 FROM goods_receipts r "
            "WHERE r.id=NEW.goods_receipt_id\n        AND "
            "r.organization_id=NEW.organization_id AND "
            "r.lines_normalized)"
        ),
        "Receipt lines are sealed",
    )


def _attempt_guards() -> None:
    _guard(
        "job_attempts_no_terminal_update",
        "job_attempts",
        "UPDATE",
        "OLD.state != 'running'",
        "Terminal attempt history is immutable",
    )
    _guard(
        "job_attempts_no_delete",
        "job_attempts",
        "DELETE",
        "1=1",
        "Attempt history is immutable",
    )
    _guard(
        "job_attempts_limit",
        "job_attempts",
        "INSERT",
        (
            "NEW.number > (\n        SELECT max_attempts FROM "
            "jobs j WHERE j.id=NEW.job_id AND "
            "j.organization_id=NEW.organization_id\n    )"
        ),
        "Attempt exceeds the configured execution limit",
    )
