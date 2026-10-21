"""Local synthetic workspace and append-only audit foundation."""

import sqlalchemy as sa
from alembic import op

revision = "0001_foundation"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "purchase_orders",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("vendor", sa.String(120), nullable=False),
        sa.Column("description", sa.String(200), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("issued_on", sa.Date(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.CheckConstraint("amount_minor >= 0 AND amount_minor <= 9007199254740991"),
    )
    op.create_table(
        "invoices",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("vendor", sa.String(120), nullable=False),
        sa.Column("description", sa.String(200), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("due_on", sa.Date(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("purchase_order_id", sa.String(32), sa.ForeignKey("purchase_orders.id")),
        sa.Column("exception_code", sa.String(64)),
        sa.Column("exception_detail", sa.Text()),
        sa.CheckConstraint("amount_minor >= 0 AND amount_minor <= 9007199254740991"),
    )
    op.create_index("ix_invoices_status", "invoices", ["status"])
    op.create_table(
        "audit_events",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("entity_id", sa.String(128), nullable=False),
        sa.Column("actor", sa.String(120), nullable=False),
        sa.Column("action", sa.String(80), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_audit_events_entity_id", "audit_events", ["entity_id"])
    op.create_table(
        "job_execution_records",
        sa.Column("key", sa.String(128), primary_key=True),
        sa.Column("result", sa.String(80), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
    )
    if op.get_bind().dialect.name == "postgresql":
        op.execute("""
            CREATE FUNCTION prevent_audit_mutation() RETURNS trigger AS $$
            BEGIN RAISE EXCEPTION 'Audit events are append-only'; END;
            $$ LANGUAGE plpgsql
        """)
        op.execute("""
            CREATE TRIGGER audit_immutable BEFORE UPDATE OR DELETE ON audit_events
            FOR EACH ROW EXECUTE FUNCTION prevent_audit_mutation()
        """)
        op.execute("""
            CREATE TRIGGER audit_no_truncate BEFORE TRUNCATE ON audit_events
            FOR EACH STATEMENT EXECUTE FUNCTION prevent_audit_mutation()
        """)
    elif op.get_bind().dialect.name == "sqlite":
        for operation in ("UPDATE", "DELETE"):
            op.execute(f"""
                CREATE TRIGGER audit_no_{operation.lower()} BEFORE {operation} ON audit_events
                BEGIN SELECT RAISE(ABORT, 'Audit events are append-only'); END
            """)


def downgrade() -> None:
    raise RuntimeError("Downgrade would destroy audit records. Restore a verified backup instead.")
