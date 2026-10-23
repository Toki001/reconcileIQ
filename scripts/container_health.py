"""Container health probes. Emit no credentials, document data or driver exceptions."""

import os
import sys
from urllib.request import urlopen


def probe(role: str) -> bool:
    if role == "api":
        with urlopen("http://127.0.0.1:8000/health/ready", timeout=8) as response:
            return bool(response.status == 200)
    if role == "worker":
        from reconcileiq_worker.app import celery_app

        node = "celery@" + os.environ["HOSTNAME"]
        replies = celery_app.control.inspect(destination=[node], timeout=5).ping()
        return isinstance(replies, dict) and replies.get(node) == {"ok": "pong"}
    return False


if __name__ == "__main__":
    try:
        sys.exit(0 if len(sys.argv) == 2 and probe(sys.argv[1]) else 1)
    except Exception:
        sys.exit(1)
