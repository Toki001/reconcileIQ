"""Upgrade an isolated copy of existing milestone 02 PostgreSQL data; retain source untouched."""

import json
import os
from collections import Counter

from alembic import command
from alembic.config import Config
from reconcileiq_database.config import get_settings
from reconcileiq_database.financial_seed import seed_financial
from reconcileiq_database.models import AuditEvent, Invoice, Job
from reconcileiq_database.testing import local_postgres_url, postgres_schema
from sqlalchemy import MetaData, create_engine, func, select, text
from sqlalchemy.dialects.postgresql import insert


def main() -> None:
    original = os.environ.get("RECONCILE_DATABASE_URL")
    source = create_engine(local_postgres_url())
    try:
        with source.connect() as connection:
            current = connection.scalar(text("SELECT version_num FROM alembic_version"))
        with postgres_schema() as (target, url):
            os.environ["RECONCILE_DATABASE_URL"] = url.render_as_string(hide_password=False)
            get_settings.cache_clear()
            configuration = Config("alembic.ini")
            command.upgrade(configuration, "0002_contracts")
            before = {}
            if current == "0002_contracts":
                metadata = MetaData()
                metadata.reflect(bind=target)
                with source.connect() as old, target.begin() as new:
                    for table in metadata.sorted_tables:
                        if table.name == "alembic_version":
                            continue
                        rows = [dict(row) for row in old.execute(select(table)).mappings()]
                        before[table.name] = len(rows)
                        if rows:
                            statement = insert(table).values(rows)
                            if table.name == "organizations":
                                statement = statement.on_conflict_do_nothing(index_elements=["id"])
                            new.execute(statement)
            else:
                with target.begin() as connection:
                    connection.execute(
                        text(
                            "INSERT INTO purchase_orders "
                            "(id,vendor,description,currency,amount_minor,issued_on,status) VALUES "
                            "('M02-PO','Synthetic legacy vendor','Preserved',"
                            "'USD',100,'2026-10-01','open')"
                        )
                    )
                    connection.execute(
                        text(
                            "INSERT INTO invoices "
                            "(id,vendor,description,currency,amount_minor,"
                            "due_on,status,purchase_order_id) VALUES "
                            "('M02-INV','Synthetic legacy vendor','Preserved',"
                            "'USD',100,'2026-11-01','matched','M02-PO')"
                        )
                    )
                    connection.execute(
                        text(
                            "INSERT INTO audit_events "
                            "(id,entity_id,actor,action,detail,occurred_at) VALUES "
                            "('M02-AUDIT','M02-INV','synthetic','created',"
                            "'Preserved original history','2026-10-01')"
                        )
                    )
            legacy = MetaData()
            legacy.reflect(bind=target)
            snapshots = {}
            with target.connect() as connection:
                for table in legacy.sorted_tables:
                    if table.name != "alembic_version":
                        snapshots[table.name] = Counter(
                            json.dumps(dict(row), sort_keys=True, default=str)
                            for row in connection.execute(select(table)).mappings()
                        )
            command.upgrade(configuration, "0004_vendor_ref")
            with target.connect() as connection:
                for table in legacy.sorted_tables:
                    if table.name == "alembic_version":
                        continue
                    retained = Counter(
                        json.dumps(dict(row), sort_keys=True, default=str)
                        for row in connection.execute(select(table)).mappings()
                    )
                    assert not snapshots[table.name] - retained, table.name
                if before:
                    assert (
                        connection.scalar(select(func.count()).select_from(Invoice))
                        == before["invoices"]
                    )
                    audit_count = connection.scalar(select(func.count()).select_from(AuditEvent))
                    assert audit_count is not None and audit_count >= before["audit_events"]
                    assert (
                        connection.scalar(select(func.count()).select_from(Job)) == before["jobs"]
                    )
            command.downgrade(configuration, "0003_financial")
            command.upgrade(configuration, "head")
            command.check(configuration)
            counts = seed_financial(target)
            assert seed_financial(target) == counts
            with target.connect() as connection:
                states = Counter(connection.execute(select(Job.state)).scalars())
                assert not states.get("succeeded")
            command.check(configuration)
            print(
                "PostgreSQL M03 smoke passed: isolated M02 upgrade, retained records, exact "
                "seed counts, repeatability, safe unused-reference downgrade/upgrade and "
                "metadata agreement."
            )
            if before:
                print("Existing M02 rows copied and retained:", before)
    finally:
        source.dispose()
        if original is None:
            os.environ.pop("RECONCILE_DATABASE_URL", None)
        else:
            os.environ["RECONCILE_DATABASE_URL"] = original
        get_settings.cache_clear()


if __name__ == "__main__":
    main()
