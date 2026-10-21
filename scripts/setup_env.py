"""Generate local-only random credentials without printing or overwriting them."""

import os
import secrets
from pathlib import Path


def main() -> None:
    path = Path(".env")
    if path.exists():
        print("Existing .env retained.")
        return
    password = {name: secrets.token_hex(24) for name in ("postgres", "rabbitmq", "minio")}
    content = (
        "# Generated local credentials. Never commit or use in production.\n"
        "RECONCILE_ENVIRONMENT=local\n"
        "RECONCILE_DATABASE_URL=sqlite:///.data/reconcileiq.db\n"
        "RECONCILE_INFRASTRUCTURE_REQUIRED=false\n"
        f"RECONCILE_BROKER_URL=amqp://reconcile:{password['rabbitmq']}@127.0.0.1:5672//\n"
        "RECONCILE_MINIO_ENDPOINT=http://127.0.0.1:9100\n"
        "POSTGRES_USER=reconcile\n"
        f"POSTGRES_PASSWORD={password['postgres']}\n"
        "POSTGRES_DB=reconcileiq\n"
        "POSTGRES_PORT=15432\n"
        "MINIO_PORT=9100\n"
        "MINIO_CONSOLE_PORT=9101\n"
        "WEB_PORT=3100\n"
        "RABBITMQ_DEFAULT_USER=reconcile\n"
        f"RABBITMQ_DEFAULT_PASS={password['rabbitmq']}\n"
        "MINIO_ROOT_USER=reconcile-local\n"
        f"MINIO_ROOT_PASSWORD={password['minio']}\n"
        "API_BASE_URL=http://127.0.0.1:8000\n"
    )
    # O_EXCL also protects against racing invocations; 0600 before any secret is written.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as output:
        output.write(content)
    Path(".data").mkdir(exist_ok=True)
    print("Created .env with random local credentials (mode 0600).")


if __name__ == "__main__":
    main()
