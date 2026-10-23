"""Release safety boundaries, independent of any GitHub account or live cloud provider."""

import os
import sys
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest
from pydantic import ValidationError
from reconcileiq_database.config import Settings

sys.path.insert(0, str(Path("scripts").absolute()))
import container_health  # noqa: E402
import release_guard  # noqa: E402


def test_production_identity_remains_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RECONCILE_ENVIRONMENT", "production")
    with pytest.raises(ValidationError):
        Settings()


@pytest.mark.parametrize("reply", [None, {}, {"celery@other": {"ok": "pong"}}])
def test_worker_health_rejects_missing_or_other_nodes(
    monkeypatch: pytest.MonkeyPatch, reply: Any
) -> None:
    from reconcileiq_worker.app import celery_app

    monkeypatch.setenv("HOSTNAME", "this-worker")
    inspect = Mock(return_value=Mock(ping=Mock(return_value=reply)))
    monkeypatch.setattr(celery_app.control, "inspect", inspect)
    assert not container_health.probe("worker")
    inspect.assert_called_once_with(destination=["celery@this-worker"], timeout=5)


def test_worker_health_accepts_only_target_pong(monkeypatch: pytest.MonkeyPatch) -> None:
    from reconcileiq_worker.app import celery_app

    monkeypatch.setenv("HOSTNAME", "this-worker")
    monkeypatch.setattr(
        celery_app.control,
        "inspect",
        Mock(return_value=Mock(ping=Mock(return_value={"celery@this-worker": {"ok": "pong"}}))),
    )
    assert container_health.probe("worker")


@pytest.mark.parametrize("ref", ["refs/heads/main", "refs/tags/v1.0", "refs/tags/v1.0.0;id"])
def test_publication_rejects_nonrelease_refs(monkeypatch: pytest.MonkeyPatch, ref: str) -> None:
    for key, value in {
        "GITHUB_REPOSITORY": "test/reconcileiq",
        "GITHUB_SHA": "a" * 40,
        "GITHUB_REF": ref,
    }.items():
        monkeypatch.setenv(key, value)
    with pytest.raises(ValueError, match="explicit"):
        release_guard.main()


def test_publication_rejects_pr_event(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in {
        "GITHUB_REPOSITORY": "test/reconcileiq",
        "GITHUB_SHA": "a" * 40,
        "GITHUB_REF": "refs/tags/v1.0.0",
        "GITHUB_EVENT_NAME": "pull_request",
    }.items():
        monkeypatch.setenv(key, value)
    with pytest.raises(ValueError, match="manual"):
        release_guard.main()
    assert os.environ["GITHUB_EVENT_NAME"] == "pull_request"
