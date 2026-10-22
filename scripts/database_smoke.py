"Check the running PostgreSQL schema, audit protection and seed repeatability."

from alembic import command
from alembic.config import Config
from reconcileiq_database.config import get_settings
from reconcileiq_database.models import AuditEvent, Invoice, PurchaseOrder
from reconcileiq_database.seed import seed_demo
from reconcileiq_database.session import build_engine
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session


def main() -> None:
    engine = build_engine(get_settings())
    try:
        assert engine.dialect.name == "postgresql", "This smoke check requires PostgreSQL"
        command.check(Config("alembic.ini"))
        with Session(engine) as session:
            before = session.scalar(select(func.count()).select_from(AuditEvent))
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(Invoice)
                    .where(Invoice.synthetic.is_(True), Invoice.organization_id == "demo")
                )
                == 6
            )
            po_count = session.scalar(select(func.count()).select_from(PurchaseOrder))
            assert po_count is not None and po_count >= 6
            assert before is not None and before >= 12
        seed_demo(engine)
        # Each attempted mutation is in its own transaction. Even if a trigger is
        # missing, the assertion rolls the operation back; no history is erased.
        for statement in (
            "UPDATE audit_events SET actor='smoke-test' WHERE id='seed-INV-1042'",
            "DELETE FROM audit_events WHERE id='seed-INV-1042'",
            "TRUNCATE audit_events",
        ):
            try:
                with engine.begin() as connection:
                    connection.execute(text(statement))
                    raise AssertionError("Audit mutation was not blocked by the database")
            except DBAPIError as exc:
                assert "append-only" in str(exc.orig)
        with Session(engine) as session:
            assert session.scalar(select(func.count()).select_from(AuditEvent)) == before
        print("PostgreSQL smoke passed: schema, seed repeatability, append-only audit triggers.")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
