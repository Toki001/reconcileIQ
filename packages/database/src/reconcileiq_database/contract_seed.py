"Local credential provisioning and explicitly synthetic GraphQL result examples."

import hashlib
from datetime import UTC, datetime

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from reconcileiq_database.config import Settings
from reconcileiq_database.financial import ensure_identity
from reconcileiq_database.models import (
    AuditEvent,
    Credential,
    Discrepancy,
    Invoice,
    ReconciliationRun,
)


def seed_contracts(engine: Engine, settings: Settings) -> None:
    with Session(engine) as session, session.begin():
        for role, token in (
            ("admin", settings.admin_token),
            ("reviewer", settings.reviewer_token),
            ("auditor", settings.auditor_token),
        ):
            if not token.get_secret_value():
                continue
            digest = hashlib.sha256(token.get_secret_value().encode()).hexdigest()
            if session.get(Credential, digest) is None:
                session.add(
                    Credential(
                        digest=digest,
                        organization_id="demo",
                        actor=f"local-{role}",
                        role=role,
                        user_id=ensure_identity(session, "demo", f"local-{role}", role),
                    )
                )
                session.add(
                    AuditEvent(
                        id=f"credential-{digest[:32]}",
                        organization_id="demo",
                        entity_id=f"local-{role}",
                        actor="local-bootstrap",
                        action="credential_provisioned",
                        detail=(
                            f"Local {role} API credential provisioned; "
                            "token retained only in ignored environment files."
                        ),
                        occurred_at=datetime.now(UTC),
                    )
                )
        for row in session.scalars(
            select(Invoice)
            .where(Invoice.organization_id == "demo", Invoice.synthetic.is_(True))
            .limit(100)
        ):
            run_id = f"DEMO-RUN-{row.id}"
            if session.get(ReconciliationRun, run_id):
                continue
            session.add(
                ReconciliationRun(
                    id=run_id,
                    organization_id="demo",
                    invoice_id=row.id,
                    status="completed",
                    policy_id="three_way_exact_v1",
                    outcome=row.status,
                    synthetic=True,
                    created_at=datetime(2026, 10, 21, 10, tzinfo=UTC),
                )
            )
            session.flush()
            session.add(
                AuditEvent(
                    id=f"seed-{run_id}",
                    organization_id="demo",
                    entity_id=run_id,
                    actor="local-seed",
                    action="synthetic_result_created",
                    detail=(
                        "Explicit synthetic result example, not an executed reconciliation or "
                        "spending authorization."
                    ),
                    occurred_at=datetime(2026, 10, 21, 10, tzinfo=UTC),
                )
            )
            if row.exception_code:
                discrepancy_id = f"DEMO-DISC-{row.id}"
                session.add(
                    Discrepancy(
                        id=discrepancy_id,
                        organization_id="demo",
                        invoice_id=row.id,
                        reconciliation_id=run_id,
                        code=row.exception_code,
                        detail=row.exception_detail or "Synthetic example.",
                        field="quantity"
                        if row.exception_code == "QUANTITY_MISMATCH"
                        else "document"
                        if row.exception_code == "POSSIBLE_DUPLICATE"
                        else "amount_minor",
                        expected=None,
                        observed=None,
                        currency=row.currency,
                        created_at=datetime(2026, 10, 21, 10, tzinfo=UTC),
                    )
                )
                session.add(
                    AuditEvent(
                        id=f"seed-{discrepancy_id}",
                        organization_id="demo",
                        entity_id=discrepancy_id,
                        actor="local-seed",
                        action="synthetic_discrepancy_created",
                        detail=(
                            "Synthetic discrepancy linked to an explicitly synthetic "
                            "reconciliation "
                            "result."
                        ),
                        occurred_at=datetime(2026, 10, 21, 10, tzinfo=UTC),
                    )
                )
