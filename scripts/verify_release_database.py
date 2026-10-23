"""Local-only backup/restore and populated-copy migration check; never resets retained data."""

import argparse
import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

from alembic import command
from alembic.config import Config
from dotenv import dotenv_values
from reconcileiq_database.testing import local_postgres_url, postgres_database
from sqlalchemy import Engine, MetaData, select
from sqlalchemy.engine import URL


def snapshot(engine: Engine) -> dict[str, dict[str, str]]:
    metadata = MetaData()
    metadata.reflect(engine)
    result: dict[str, dict[str, str]] = {}
    with engine.connect() as connection:
        for name, table in metadata.tables.items():
            rows = {}
            for row in connection.execute(select(table)).mappings():
                identity = json.dumps([row[c.name] for c in table.primary_key], default=str)
                rows[identity] = hashlib.sha256(
                    json.dumps(dict(row), sort_keys=True, default=str).encode()
                ).hexdigest()
            result[name] = rows
    return result


def docker(arguments: list[str], data: bytes | None = None) -> bytes:
    result = subprocess.run(
        ["docker", "compose", "exec", "-T", "postgres", *arguments],
        input=data,
        capture_output=True,
        timeout=120,
    )
    if result.returncode:
        raise RuntimeError("Local PostgreSQL backup/restore command failed; diagnostics withheld")
    return result.stdout


def verify_baseline(image: str, url: URL) -> None:
    from reconcileiq_database.config import get_settings

    settings = get_settings()
    required = {
        "RECONCILE_ADMIN_TOKEN",
        "RECONCILE_REVIEWER_TOKEN",
        "RECONCILE_AUDITOR_TOKEN",
        "RECONCILE_CURSOR_SECRET",
        "RECONCILE_SUPPLIER_WEBHOOKS",
        "RECONCILE_MINIO_ACCESS_KEY",
        "RECONCILE_MINIO_SECRET_KEY",
        "RECONCILE_MINIO_BUCKET",
    }
    # Never forward unrelated host credentials or AI keys into the baseline probe.
    values = {
        key: value
        for key, value in dotenv_values(".env").items()
        if key in required and value is not None
    }
    broker = urlsplit(settings.broker_url.get_secret_value())
    values.update(
        {
            "RECONCILE_ENVIRONMENT": "local",
            "RECONCILE_AI_ENABLED": "false",
            "RECONCILE_INFRASTRUCTURE_REQUIRED": "true",
            "RECONCILE_WORKER_ENABLED": "true",
            "RECONCILE_DATABASE_URL": url.set(host="postgres", port=5432).render_as_string(
                hide_password=False
            ),
            "RECONCILE_BROKER_URL": urlunsplit(
                broker._replace(netloc=broker.netloc.rsplit("@", 1)[0] + "@rabbitmq:5672")
            ),
            "RECONCILE_MINIO_ENDPOINT": "http://minio:9000",
        }
    )
    path = Path(".cache/m07-baseline-" + uuid4().hex + ".env")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as stream:
        stream.write("".join(f"{key}={value}\n" for key, value in values.items()))
    try:
        code = (
            "import runpy; runpy.run_path('scripts/database_smoke.py',run_name='__main__'); "
            "from reconcileiq_api.main import app; "
            "ready=next(r.endpoint for r in app.routes "
            "if getattr(r,'path',None)=='/health/ready'); "
            "assert ready().status == 'ok'"
        )
        result = subprocess.run(
            [
                "docker",
                "run",
                "--pull=never",
                "--rm",
                "--network",
                "reconcileiq_default",
                "--env-file",
                str(path),
                image,
                "python",
                "-c",
                code,
            ],
            capture_output=True,
            timeout=90,
        )
        if result.returncode:
            raise RuntimeError("Baseline image compatibility check failed; diagnostics withheld")
    finally:
        path.unlink()


def main() -> None:
    from sqlalchemy import create_engine

    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline-image", help="Already-built local M06 image; never pulls")
    args = parser.parse_args()
    url = local_postgres_url()
    if url.host not in {"127.0.0.1", "localhost"} or url.port != 15432:
        raise ValueError("Release verification requires local ReconcileIQ PostgreSQL:15432")
    source = create_engine(url)
    before = snapshot(source)
    backup = Path(".cache/m07-pre-release-" + uuid4().hex + ".dump")
    backup.parent.mkdir(exist_ok=True)
    # Exclusive create preserves all prior backups; restore the exact bytes saved below.
    payload = docker(
        ["sh", "-c", 'exec pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom']
    )
    if not backup.exists():
        fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
    with postgres_database() as (restored, restored_url):
        name = restored_url.database
        assert name and name.startswith("riq_m05_test_")
        docker(
            ["sh", "-c", 'exec pg_restore -U "$POSTGRES_USER" -d "$1" --exit-on-error', "sh", name],
            backup.read_bytes(),
        )
        if snapshot(restored) != before:
            raise AssertionError("Restored copy does not match retained source")
        # Independent fresh Alembic config bound to this disposable database only.
        from reconcileiq_database.config import get_settings

        previous = os.environ.get("RECONCILE_DATABASE_URL")
        os.environ["RECONCILE_DATABASE_URL"] = restored_url.render_as_string(hide_password=False)
        get_settings.cache_clear()
        try:
            command.upgrade(Config("alembic.ini"), "head")
            command.upgrade(Config("alembic.ini"), "head")
            command.check(Config("alembic.ini"))
        finally:
            if previous is None:
                os.environ.pop("RECONCILE_DATABASE_URL", None)
            else:
                os.environ["RECONCILE_DATABASE_URL"] = previous
            get_settings.cache_clear()
        if args.baseline_image:
            verify_baseline(args.baseline_image, restored_url)
        if snapshot(restored) != before:
            raise AssertionError(
                "Migration or baseline image changed retained copy rows unexpectedly"
            )
        if snapshot(source) != before:
            raise AssertionError("Retained database changed during release verification")
    source.dispose()
    result: dict[str, Any] = {
        "backup_bytes": len(payload),
        "backup_mode": oct(backup.stat().st_mode & 0o777),
        "tables": len(before),
        "rows": sum(len(rows) for rows in before.values()),
        "restore_equal": True,
        "head_upgrade_repeatable": True,
        "metadata_consistent": True,
        "baseline_image_compatible": bool(args.baseline_image),
        "retained_source_unchanged": True,
        "scope": "No new migration in milestone 07; upgrade is head-to-head on populated copy",
    }
    print(json.dumps(result))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(
            f"Release database verification failed ({type(error).__name__}); diagnostics withheld."
        )
        raise SystemExit(1) from None
