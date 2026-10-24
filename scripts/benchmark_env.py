"""Explicitly isolated local M08 runner. Never changes the retained Compose database."""

import argparse
import os
import secrets
import subprocess
from pathlib import Path

from dotenv import dotenv_values
from sqlalchemy.engine import URL

DIRECTORY = Path(".cache/m08")


def environment() -> dict[str, str]:
    path = DIRECTORY / ".env"
    if not path.exists():
        raise ValueError("Run benchmark_env.py init first")
    values = {key: value for key, value in dotenv_values(path).items() if value is not None}
    result = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("RECONCILE_", "GOOGLE_", "GEMINI_"))
    }
    result.update(values)
    result.update(
        {
            "COMPOSE_FILE": "compose.benchmark.yml:compose.observability.yml",
            "COMPOSE_PROJECT_NAME": "reconcileiq-m08",
            "COMPOSE_PROFILES": "observability",
            "RECONCILE_DATABASE_URL": URL.create(
                "postgresql+psycopg",
                username=values["POSTGRES_USER"],
                password=values["POSTGRES_PASSWORD"],
                host="127.0.0.1",
                port=25432,
                database=values["POSTGRES_DB"],
            ).render_as_string(hide_password=False),
            "RECONCILE_BROKER_URL": f"amqp://{values['RABBITMQ_DEFAULT_USER']}:{values['RABBITMQ_DEFAULT_PASS']}@127.0.0.1:25672//",
            "RECONCILE_MINIO_ENDPOINT": "http://127.0.0.1:29100",
            "RECONCILE_MINIO_PUBLIC_ENDPOINT": "http://127.0.0.1:29100",
            "RECONCILE_TEST_POSTGRES_PORT": "25432",
            "RECONCILE_TEST_NETWORK": "reconcileiq-m08_default",
            "RECONCILE_TEST_PYTHON_IMAGE": "reconcileiq-python:m08",
            "RECONCILE_TEST_WORKER_IMAGE": "reconcileiq-worker:m08",
            "RECONCILE_TEST_API_URL": "http://127.0.0.1:28000",
            "PLAYWRIGHT_BASE_URL": "http://127.0.0.1:23100",
            "RECONCILE_TEST_ENV_FILE": str(path),
            "RECONCILE_AI_ENABLED": "false",
            "RECONCILE_AI_API_KEY": "",
            "RECONCILE_TELEMETRY_ENABLED": "false",
            "RECONCILE_OBSERVABILITY_ACTIVE": "true",
            "RECONCILE_TELEMETRY_ENVIRONMENT": "benchmark",
            "RECONCILE_TELEMETRY_ENDPOINT": "http://127.0.0.1:14318",
            "RECONCILE_WORKER_ENABLED": "false",
            "RECONCILE_INFRASTRUCTURE_REQUIRED": "false",
        }
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["init", "run", "compose"])
    parser.add_argument("arguments", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.action == "init":
        (DIRECTORY / "apps/web").mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["python3", str(Path("scripts/setup_env.py").absolute())], cwd=DIRECTORY, check=True
        )
        path = DIRECTORY / ".env"
        values = dotenv_values(path)
        if "RECONCILE_GRAFANA_PASSWORD" not in values:
            with path.open("a") as stream:
                stream.write("RECONCILE_GRAFANA_PASSWORD=" + secrets.token_hex(32) + "\n")
        print("Independent benchmark credentials prepared (0600); no secrets printed.")
        return
    if not args.arguments:
        raise ValueError("Command required")
    command = ["docker", "compose", *args.arguments] if args.action == "compose" else args.arguments
    raise SystemExit(subprocess.run(command, env=environment(), check=False).returncode)


if __name__ == "__main__":
    main()
