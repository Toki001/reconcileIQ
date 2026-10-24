"Bootstrap SQLite and exercise the actual ASGI handlers, without external services."

import os
import subprocess
import sys
from pathlib import Path


def main() -> None:
    database = Path(os.environ.get("RECONCILE_LITE_DATABASE_PATH", ".data/reconcileiq.db"))
    database.parent.mkdir(parents=True, exist_ok=True)
    os.environ["RECONCILE_DATABASE_URL"] = "sqlite:///" + str(database)
    os.environ["RECONCILE_INFRASTRUCTURE_REQUIRED"] = "false"
    os.environ["RECONCILE_WORKER_ENABLED"] = "false"
    for command in (["alembic", "upgrade", "head"], ["reconcileiq_database.seed"]):
        subprocess.run([sys.executable, "-m", *command], check=True)
    from fastapi.testclient import TestClient
    from reconcileiq_api.main import app
    from reconcileiq_database.config import get_settings

    with TestClient(app) as client:
        assert client.get("/health/live").status_code == 200
        assert client.get("/health/ready").status_code == 200
        response = client.get(
            "/v1/demo/snapshot",
            headers={"Authorization": f"Bearer {get_settings().auditor_token.get_secret_value()}"},
        )
        response.raise_for_status()
        assert len(response.json()["invoices"]) == 6
    print("Lite bootstrap passed: migration, seed, liveness, database readiness and six invoices.")


if __name__ == "__main__":
    main()
