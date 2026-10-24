"""Read-only snapshots and post-commit counters; never use metrics as financial authority."""

from collections.abc import Iterable
from contextlib import suppress
from datetime import UTC, datetime
from typing import Any, cast

from opentelemetry.metrics import CallbackOptions, Observation
from reconcileiq_telemetry import metric, runtime
from sqlalchemy import Engine, event, inspect, text
from sqlalchemy.orm import Session, SessionTransaction

_INSTALLED = False


def committed_metrics() -> None:
    global _INSTALLED
    if _INSTALLED or not runtime().enabled:
        return
    _INSTALLED = True

    def collect(session: Session, context: Any) -> None:
        transaction = session.get_nested_transaction() or session.get_transaction()
        pending = session.info.setdefault("riq_states", {}).setdefault(transaction, {})
        for entity in session.new | session.dirty:
            if type(entity).__name__ == "Job" and (
                entity in session.new or inspect(entity).attrs.state.history.has_changes()
            ):
                pending[entity.id] = (
                    entity.state,
                    entity.attempts,
                    entity.created_at,
                    entity.updated_at,
                    entity.error_code,
                )

    def committed(session: Session) -> None:
        transaction = session.get_nested_transaction() or session.get_transaction()
        buckets = session.info.get("riq_states", {})
        pending = buckets.pop(transaction, {})
        if transaction is not None and transaction.nested:
            # Releasing a savepoint is not a durable commit. Merge into its parent;
            # outer rollback must still discard every pending observation.
            buckets.setdefault(transaction.parent, {}).update(pending)
            return
        session.info.pop("riq_states", None)
        for state, attempts, created, updated, error_code in pending.values():
            metric("riq.jobs.transitions", labels={"job_state": state})
            if state in {"succeeded", "failed"}:
                metric("riq.jobs.completed", labels={"outcome": state})
            if created and updated:
                duration = (
                    updated.replace(tzinfo=UTC) - created.replace(tzinfo=UTC)
                ).total_seconds()
                if state == "running" and attempts == 1:
                    metric("riq.queue.wait", max(0, duration), kind="histogram", unit="s")
                elif state in {"succeeded", "failed"}:
                    metric(
                        "riq.jobs.total.duration",
                        max(0, duration),
                        {"outcome": state},
                        kind="histogram",
                        unit="s",
                    )
            if state in {"pending_dispatch", "failed"}:
                if error_code == "CHECKSUM_MISMATCH":
                    metric("riq.document.checksum.failures")
                if error_code in {"INVALID_PDF", "ENCRYPTED_PDF", "OCR_REQUIRED", "PDF_PAGE_LIMIT"}:
                    metric("riq.document.unsupported")
            if state == "pending_dispatch" and attempts:
                metric("riq.jobs.retries")

    def rollback(session: Session) -> None:
        transaction = session.get_nested_transaction() or session.get_transaction()
        session.info.get("riq_states", {}).pop(transaction, None)

    def ended(session: Session, transaction: SessionTransaction) -> None:
        if transaction.parent is None:
            session.info.pop("riq_states", None)

    def safe(function: Any) -> Any:
        def observer(*args: Any, **kwargs: Any) -> None:
            with suppress(Exception):
                function(*args, **kwargs)

        return observer

    event.listen(Session, "after_flush", safe(collect))
    event.listen(Session, "after_commit", safe(committed))
    event.listen(Session, "after_rollback", safe(rollback))
    event.listen(Session, "after_transaction_end", safe(ended))


def gauges(engine: Engine) -> None:
    current = runtime()
    if not current.enabled or current.meters is None:
        return
    meter = current.meters.get_meter("reconcileiq")

    def jobs(options: CallbackOptions) -> Iterable[Observation]:
        try:
            with engine.connect() as connection:
                if engine.dialect.name == "postgresql":
                    connection.exec_driver_sql("SET LOCAL statement_timeout='1000ms'")
                rows = connection.execute(
                    text("SELECT state,count(*) FROM jobs GROUP BY state")
                ).all()
            counts: dict[str, int] = dict(rows)
            # Emit explicit zeros when a state drains. Omitting it leaves the
            # Collector's previous nonzero value visible until metric expiration.
            for state in ("pending_dispatch", "queued", "running", "succeeded", "failed"):
                yield Observation(counts.get(state, 0), {"job_state": state})
        except Exception:
            metric("riq.dependency.failures", labels={"dependency": "database"})

    meter.create_observable_gauge("riq.jobs", callbacks=[jobs], unit="")
    queries = {
        "riq.outbox.pending": "SELECT count(*) FROM outbox_events WHERE published_at IS NULL",
        "riq.jobs.retry_exhausted": (
            "SELECT count(*) FROM jobs WHERE state='failed' AND attempts>=max_attempts"
        ),
        "riq.review.backlog": (
            "SELECT count(*) FROM invoices WHERE status IN ('needs_review','exception')"
        ),
        "riq.validation.failures": (
            "SELECT count(*) FROM discrepancies WHERE code='INVOICE_TOTAL_INCONSISTENCY'"
        ),
    }
    for name, query in queries.items():

        def snapshot(options: CallbackOptions, query: str = query) -> Iterable[Observation]:
            try:
                with engine.connect() as connection:
                    if engine.dialect.name == "postgresql":
                        connection.exec_driver_sql("SET LOCAL statement_timeout='1000ms'")
                    yield Observation(int(connection.scalar(text(query)) or 0))
            except Exception:
                return

        meter.create_observable_gauge(name, callbacks=[snapshot], unit="")

    def age(options: CallbackOptions) -> Iterable[Observation]:
        try:
            with engine.connect() as connection:
                oldest = connection.scalar(
                    text(
                        "SELECT min(created_at) FROM jobs WHERE state IN "
                        "('pending_dispatch','queued')"
                    )
                )
            if isinstance(oldest, str):
                oldest = datetime.fromisoformat(oldest)
            yield Observation(
                max(0, (datetime.now(UTC) - oldest.replace(tzinfo=UTC)).total_seconds())
                if oldest
                else 0
            )
        except Exception:
            return

    meter.create_observable_gauge("riq.jobs.oldest_pending_age", callbacks=[age], unit="s")

    def matching(options: CallbackOptions) -> Iterable[Observation]:
        try:
            with engine.connect() as connection:
                rows = connection.execute(
                    text("SELECT outcome,count(*) FROM reconciliation_runs GROUP BY outcome")
                ).all()
            for outcome, count in rows:
                if outcome in {
                    "matched",
                    "unmatched",
                    "duplicate",
                    "discrepancy",
                    "needs_review",
                    "extraction_failed",
                }:
                    yield Observation(cast(int, count), {"outcome": outcome})
        except Exception:
            return

    meter.create_observable_gauge("riq.reconciliation.results", callbacks=[matching], unit="")

    def discrepancies(options: CallbackOptions) -> Iterable[Observation]:
        try:
            with engine.connect() as connection:
                rows = connection.execute(
                    text("SELECT code,count(*) FROM discrepancies GROUP BY code")
                ).all()
            totals: dict[str, int] = {}
            for code, count in rows:
                code = str(code)
                category = next(
                    (
                        key
                        for key in ["price", "quantity", "tax", "currency", "supplier"]
                        if key.upper() in code
                    ),
                    "other",
                )
                totals[category] = totals.get(category, 0) + cast(int, count)
            yield from (Observation(count, {"category": key}) for key, count in totals.items())
        except Exception:
            return

    meter.create_observable_gauge(
        "riq.reconciliation.discrepancies", callbacks=[discrepancies], unit=""
    )
