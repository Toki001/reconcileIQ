"""Bootstrap SQLite and exercise the actual ASGI handlers, without external services."""

import os
import subprocess
import sys
from pathlib import Path


def main() -> None:
    Path(".data").mkdir(exist_ok=True)
    os.environ["RECONCILE_DATABASE_URL"] = "sqlite:///.data/reconcileiq.db"
    os.environ["RECONCILE_INFRASTRUCTURE_REQUIRED"] = "false"
    for command in (["alembic", "upgrade", "head"], ["reconcileiq_database.seed"]):
        subprocess.run([sys.executable, "-m", *command], check=True)
    from fastapi.testclient import TestClient
    from reconcileiq_api.main import app

    with TestClient(app) as client:
        assert client.get("/health/live").status_code == 200
        assert client.get("/health/ready").status_code == 200
        response = client.get("/v1/demo/snapshot")
        response.raise_for_status()
        assert len(response.json()["invoices"]) == 6
    print("Lite bootstrap passed: migration, seed, liveness, database readiness and six invoices.")


if __name__ == "__main__":
    main()
