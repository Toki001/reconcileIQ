"""Retain seals after newer extraction versions replace the selected version."""

import sqlalchemy as sa
from alembic import op

revision = "0005_evidence_seals"
down_revision = "0004_vendor_ref"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "extraction_version_seals",
        sa.Column(
            "organization_id", sa.String(32), sa.ForeignKey("organizations.id"), primary_key=True
        ),
        sa.Column("extraction_version_id", sa.String(32), primary_key=True),
        sa.Column("invoice_id", sa.String(32), nullable=False),
        sa.Column(
            "sealed_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.current_timestamp(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "extraction_version_id", "invoice_id"],
            [
                "extraction_versions.organization_id",
                "extraction_versions.id",
                "extraction_versions.invoice_id",
            ],
            name="fk_extraction_version_seals_extraction_invoice",
        ),
    )
    op.create_index(
        "ix_extraction_seals_invoice", "extraction_version_seals", ["organization_id", "invoice_id"]
    )
    # Conservatively seal every version previously declared validated, including one
    # superseded before this revision. Untrusted/rejected versions remain source evidence.
    op.execute("""
        INSERT INTO extraction_version_seals (organization_id, extraction_version_id, invoice_id)
        SELECT organization_id, id, invoice_id FROM extraction_versions
        WHERE validation_state = 'validated'
    """)
    sqlite = op.get_bind().dialect.name == "sqlite"
    if not sqlite:
        op.execute("""
            CREATE FUNCTION retain_extraction_seal_fn() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
              IF NEW.financial_state = 'validated' THEN
                INSERT INTO extraction_version_seals
                  (organization_id, extraction_version_id, invoice_id)
                VALUES (NEW.organization_id, NEW.validated_extraction_id, NEW.id)
                ON CONFLICT DO NOTHING;
              END IF;
              RETURN NEW;
            END; $$
        """)
    for event in ("INSERT", "UPDATE"):
        if sqlite:
            op.execute(f"""
                CREATE TRIGGER retain_extraction_seal_{event.lower()} AFTER {event} ON invoices
                WHEN NEW.financial_state = 'validated'
                BEGIN
                  INSERT INTO extraction_version_seals
                    (organization_id, extraction_version_id, invoice_id)
                  VALUES (NEW.organization_id, NEW.validated_extraction_id, NEW.id)
                  ON CONFLICT DO NOTHING;
                END
            """)
        else:
            op.execute(f"""
                CREATE TRIGGER retain_extraction_seal_{event.lower()} AFTER {event} ON invoices
                FOR EACH ROW EXECUTE FUNCTION retain_extraction_seal_fn()
            """)
    for table in ("invoice_lines", "extracted_fields"):
        guard(
            table + "_historical_seal",
            table,
            "INSERT",
            "EXISTS (SELECT 1 FROM extraction_version_seals s "
            "WHERE s.organization_id = NEW.organization_id "
            "AND s.extraction_version_id = NEW.extraction_version_id)",
            "Published extraction history is sealed",
        )
    for event in ("UPDATE", "DELETE"):
        guard(
            "extraction_seals_no_" + event.lower(),
            "extraction_version_seals",
            event,
            "1=1",
            "Extraction version seals are immutable",
        )
    if not sqlite:
        guard(
            "extraction_seals_no_truncate",
            "extraction_version_seals",
            "TRUNCATE",
            "1=1",
            "Extraction version seals are immutable",
        )


def guard(name: str, table: str, event: str, condition: str, message: str) -> None:
    if op.get_bind().dialect.name == "sqlite":
        op.execute(f"""
            CREATE TRIGGER {name} BEFORE {event} ON {table}
            WHEN {condition} BEGIN SELECT RAISE(ABORT, '{message}'); END
        """)
    else:
        op.execute(f"""
            CREATE FUNCTION {name}_fn() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
              IF {condition} THEN RAISE EXCEPTION '{message}' USING ERRCODE='23514'; END IF;
              RETURN NEW;
            END; $$
        """)
        level = "STATEMENT" if event == "TRUNCATE" else "ROW"
        op.execute(f"""
            CREATE TRIGGER {name} BEFORE {event} ON {table}
            FOR EACH {level} EXECUTE FUNCTION {name}_fn()
        """)


def downgrade() -> None:
    if op.get_bind().scalar(sa.text("SELECT count(*) FROM extraction_version_seals")):
        raise RuntimeError(
            "Extraction version seals preserve published history; use a forward migration"
        )
    sqlite = op.get_bind().dialect.name == "sqlite"
    for event in ("insert", "update"):
        if sqlite:
            op.execute(f"DROP TRIGGER retain_extraction_seal_{event}")
        else:
            op.execute(f"DROP TRIGGER retain_extraction_seal_{event} ON invoices")
    if not sqlite:
        op.execute("DROP FUNCTION retain_extraction_seal_fn()")
    guards = [
        ("invoice_lines_historical_seal", "invoice_lines"),
        ("extracted_fields_historical_seal", "extracted_fields"),
        ("extraction_seals_no_update", "extraction_version_seals"),
        ("extraction_seals_no_delete", "extraction_version_seals"),
    ]
    if not sqlite:
        guards.append(("extraction_seals_no_truncate", "extraction_version_seals"))
    for name, table in guards:
        if sqlite:
            op.execute(f"DROP TRIGGER {name}")
        else:
            op.execute(f"DROP TRIGGER {name} ON {table}")
            op.execute(f"DROP FUNCTION {name}_fn()")
    op.drop_table("extraction_version_seals")
