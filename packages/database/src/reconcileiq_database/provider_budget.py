"""Project-wide generation admission; concurrent worker processes share durable counters."""

from datetime import UTC, datetime, timedelta
from math import ceil
from uuid import uuid4

from reconcileiq_ai.provider import AISettings, FreeProof, ProviderFailure
from sqlalchemy import Engine, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from reconcileiq_database.models import ProviderBudget


def aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def reserve(engine: Engine, settings: AISettings, proof: FreeProof) -> str:
    at = datetime.now(UTC)
    minute, day = (
        at.replace(second=0, microsecond=0),
        at.replace(hour=0, minute=0, second=0, microsecond=0),
    )
    token = uuid4().hex
    with Session(engine) as session, session.begin():
        if engine.dialect.name == "sqlite":
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        else:
            session.execute(text("SET LOCAL lock_timeout='2000ms'"))
        insert = sqlite_insert if engine.dialect.name == "sqlite" else pg_insert
        session.execute(
            insert(ProviderBudget)
            .values(
                project_id=settings.project_id,
                minute_start=minute,
                day_start=day,
                minute_used=0,
                day_used=0,
                leases={},
            )
            .on_conflict_do_nothing(index_elements=["project_id"])
        )
        budget = session.get(ProviderBudget, settings.project_id, with_for_update=True)
        assert budget is not None
        if aware(budget.minute_start) != minute:
            budget.minute_start, budget.minute_used = minute, 0
        if aware(budget.day_start) != day:
            budget.day_start, budget.day_used = day, 0
        live = {
            key: value for key, value in budget.leases.items() if datetime.fromisoformat(value) > at
        }
        if len(live) >= settings.concurrency:
            delay = max(
                1,
                ceil(
                    (
                        min(datetime.fromisoformat(value) for value in live.values()) - at
                    ).total_seconds()
                ),
            )
            raise ProviderFailure("AI_CONCURRENCY_LIMIT", True, delay)
        if budget.day_used >= min(settings.requests_per_day, proof.quota_per_day):
            raise ProviderFailure(
                "AI_DAILY_LIMIT", True, ceil((day + timedelta(days=1) - at).total_seconds())
            )
        if budget.minute_used >= min(settings.requests_per_minute, proof.quota_per_minute):
            raise ProviderFailure(
                "AI_MINUTE_LIMIT", True, ceil((minute + timedelta(minutes=1) - at).total_seconds())
            )
        budget.minute_used += 1
        budget.day_used += 1
        budget.leases = {**live, token: (at + timedelta(seconds=90)).isoformat()}
    return token


def release(engine: Engine, project_id: str, token: str) -> None:
    with Session(engine) as session, session.begin():
        budget = session.get(ProviderBudget, project_id, with_for_update=True)
        if budget:
            budget.leases = {key: value for key, value in budget.leases.items() if key != token}
