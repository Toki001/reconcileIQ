"Generate/extend local secrets without printing or replacing existing credentials."

import json
import os
import secrets
from pathlib import Path


def extend(path: Path, defaults: dict[str, str]) -> dict[str, str]:
    existing = path.read_text() if path.exists() else ""
    values = {
        key: value
        for line in existing.splitlines()
        if line and not line.startswith("#") and "=" in line
        for key, value in [line.split("=", 1)]
    }
    additions = {key: value for key, value in defaults.items() if key not in values}
    flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT
    fd = os.open(path, flags, 0o600)
    with os.fdopen(fd, "w") as output:
        if additions:
            output.write(
                ("\n" if existing and not existing.endswith("\n") else "")
                + "".join(f"{key}={value}\n" for key, value in additions.items())
            )
    os.chmod(path, 0o600)
    return values | additions


def main() -> None:
    password = {name: secrets.token_hex(24) for name in ("postgres", "rabbitmq", "minio")}
    values = extend(
        Path(".env"),
        {
            "RECONCILE_ENVIRONMENT": "local",
            "RECONCILE_DATABASE_URL": "sqlite:///.data/reconcileiq.db",
            "RECONCILE_INFRASTRUCTURE_REQUIRED": "false",
            "RECONCILE_BROKER_URL": f"amqp://reconcile:{password['rabbitmq']}@127.0.0.1:5672//",
            "RECONCILE_MINIO_ENDPOINT": "http://127.0.0.1:9100",
            "POSTGRES_USER": "reconcile",
            "POSTGRES_PASSWORD": password["postgres"],
            "POSTGRES_DB": "reconcileiq",
            "POSTGRES_PORT": "15432",
            "MINIO_PORT": "9100",
            "MINIO_CONSOLE_PORT": "9101",
            "WEB_PORT": "3100",
            "RABBITMQ_DEFAULT_USER": "reconcile",
            "RABBITMQ_DEFAULT_PASS": password["rabbitmq"],
            "MINIO_ROOT_USER": "reconcile-local",
            "MINIO_ROOT_PASSWORD": password["minio"],
            "API_BASE_URL": "http://127.0.0.1:8000",
            **{
                f"RECONCILE_{role}_TOKEN": secrets.token_hex(32)
                for role in ("ADMIN", "REVIEWER", "AUDITOR")
            },
            "RECONCILE_CURSOR_SECRET": secrets.token_hex(32),
            "RECONCILE_WEB_SESSION_SECRET": secrets.token_hex(32),
            "RECONCILE_AI_ENABLED": "false",
            "RECONCILE_OCR_ENABLED": "false",
            "RECONCILE_SUPPLIER_WEBHOOKS": json.dumps(
                {"local-supplier": {"organization_id": "demo", "secret": secrets.token_hex(32)}},
                separators=(",", ":"),
            ),
        },
    )
    extend(
        Path(".env"),
        {
            "RECONCILE_MINIO_ACCESS_KEY": values["MINIO_ROOT_USER"],
            "RECONCILE_MINIO_SECRET_KEY": values["MINIO_ROOT_PASSWORD"],
            "RECONCILE_MINIO_PUBLIC_ENDPOINT": "http://127.0.0.1:9100",
            "RECONCILE_MINIO_BUCKET": "invoices",
        },
    )
    extend(
        Path("apps/web/.env.local"),
        {
            "API_BASE_URL": values["API_BASE_URL"],
            "RECONCILE_WEB_API_TOKEN": values["RECONCILE_AUDITOR_TOKEN"],
            "RECONCILE_WEB_SESSION_SECRET": values["RECONCILE_WEB_SESSION_SECRET"],
        },
    )
    Path(".data").mkdir(exist_ok=True)
    print(
        "Local environment files prepared (0600). Existing values retained; no credentials printed."
    )


if __name__ == "__main__":
    main()
