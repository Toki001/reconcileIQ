import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
from google.genai import errors
from pydantic import SecretStr, ValidationError
from reconcileiq_ai.provider import (
    FREE_MODEL,
    AISettings,
    FreeProof,
    GeminiProvider,
    ProviderFailure,
    key_fingerprint,
    verify_free_project,
)
from reconcileiq_database.models import ProviderBudget
from reconcileiq_database.provider_budget import release, reserve
from sqlalchemy import Engine
from sqlalchemy.orm import Session
from test_structured_evidence import ROWS

TEST_OPTIONS: dict[str, Any] = {"_env_file": None}


def config(**overrides: Any) -> AISettings:
    return AISettings(
        **TEST_OPTIONS,
        enabled=True,
        api_key=SecretStr("synthetic-test-key"),
        project_id="synthetic-free-project",
        **overrides,
    )


def proof(settings: AISettings) -> FreeProof:
    return FreeProof(
        settings.project_id, key_fingerprint(settings), FREE_MODEL, datetime.now(UTC), 2, 20
    )


def sdk(monkeypatch: pytest.MonkeyPatch, **changes: Any) -> MagicMock:
    models = MagicMock()
    models.count_tokens.return_value = SimpleNamespace(total_tokens=100)
    models.generate_content.return_value = SimpleNamespace(
        text=json.dumps(ROWS[0]["expected"]),
        usage_metadata=SimpleNamespace(
            prompt_token_count=100, candidates_token_count=200, thoughts_token_count=10
        ),
    )
    for key, value in changes.items():
        setattr(models, key, value)
    client = MagicMock()
    client.__enter__.return_value = SimpleNamespace(models=models)
    monkeypatch.setattr("reconcileiq_ai.provider.sdk_client", lambda _: client)
    return models


def test_verified_structured_sdk_call_is_bounded_and_tool_free(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    models = sdk(monkeypatch)
    settings = config()
    result = GeminiProvider(settings, proof(settings), synthetic_authorized=True).extract(
        {1: ROWS[0]["source_pages"]["1"]}
    )
    assert result.input_tokens == 100 and result.output_tokens == 210
    call = models.generate_content.call_args.kwargs
    assert call["model"] == FREE_MODEL and call["config"].tools == []
    assert call["config"].automatic_function_calling.disable
    assert call["config"].max_output_tokens == 8192


@pytest.mark.parametrize(
    "issue,expected",
    [
        ("synthetic", "AI_SYNTHETIC_INPUT_REQUIRED"),
        ("proof", "FREE_TIER_PROOF_EXPIRED"),
        ("size", "AI_INPUT_LIMIT"),
        ("tokens", "AI_TOKEN_LIMIT"),
        ("schema", "AI_INVALID_RESPONSE"),
        ("usage", "AI_USAGE_UNAVAILABLE"),
        ("timeout", "AI_TIMEOUT"),
        ("auth", "AI_AUTHENTICATION_FAILED"),
        ("model", "AI_MODEL_OR_REQUEST_INVALID"),
        ("quota", "AI_QUOTA_EXHAUSTED"),
        ("httpdate", "AI_QUOTA_EXHAUSTED"),
        ("longquota", "AI_QUOTA_EXHAUSTED"),
        ("server", "AI_PROVIDER_UNAVAILABLE"),
    ],
)
def test_provider_failure_mapping(
    monkeypatch: pytest.MonkeyPatch, issue: str, expected: str
) -> None:
    models = sdk(monkeypatch)
    settings = config()
    verified = proof(settings)
    pages = {1: ROWS[0]["source_pages"]["1"]}
    if issue == "proof":
        verified = FreeProof(
            settings.project_id,
            key_fingerprint(settings),
            FREE_MODEL,
            datetime.now(UTC) - timedelta(minutes=6),
            2,
            20,
        )
    elif issue == "size":
        pages = {1: "x" * 21000}
    elif issue == "tokens":
        models.count_tokens.return_value.total_tokens = 999999
    elif issue == "schema":
        models.generate_content.return_value.text = '{"approval":true}'
    elif issue == "usage":
        models.generate_content.return_value.usage_metadata = None
    elif issue == "timeout":
        models.generate_content.side_effect = TimeoutError("sensitive")
    elif issue in {"auth", "model", "quota", "httpdate", "longquota", "server"}:
        code = {
            "auth": 403,
            "model": 404,
            "quota": 429,
            "httpdate": 429,
            "longquota": 429,
            "server": 503,
        }[issue]
        from email.utils import format_datetime

        import httpx

        delay = (
            "90"
            if issue == "quota"
            else "90000"
            if issue == "longquota"
            else format_datetime(datetime.now(UTC) + timedelta(seconds=70), usegmt=True)
        )
        models.generate_content.side_effect = errors.APIError(
            code,
            {"error": {"message": "sensitive"}},
            httpx.Response(code, headers={"Retry-After": delay}),
        )
    with pytest.raises(ProviderFailure) as caught:
        GeminiProvider(settings, verified, synthetic_authorized=issue != "synthetic").extract(pages)
    assert caught.value.code == expected and "sensitive" not in str(caught.value)
    if issue in {"quota", "httpdate"}:
        assert caught.value.retry_after >= 69
    if issue == "longquota":
        assert not caught.value.retryable


def test_no_paid_or_ambient_endpoint_fallback() -> None:
    for values in ({"model": "paid-model"}, {"endpoint": "https://evil.invalid"}):
        with pytest.raises(ValidationError):
            config(**values)
    with pytest.raises(ProviderFailure, match="AI_NOT_CONFIGURED"):
        verify_free_project(AISettings(**TEST_OPTIONS, enabled=False))


@pytest.mark.parametrize(
    "issue", ["none", "billing", "account", "binding", "quota", "model", "permission"]
)
def test_read_only_free_project_verification(monkeypatch: pytest.MonkeyPatch, issue: str) -> None:
    settings = config()
    sdk_models = sdk(monkeypatch)
    sdk_models.list.return_value = (
        []
        if issue == "model"
        else [SimpleNamespace(name="models/" + FREE_MODEL, supported_actions=["generateContent"])]
    )
    monkeypatch.setattr("google.auth.default", lambda **_: (object(), None))
    responses = [
        {"name": "projects/12345"},
        {
            "billingEnabled": issue == "billing",
            "billingAccountName": "accounts/test" if issue == "account" else "",
        },
        {
            "parent": "projects/999/locations/global"
            if issue == "binding"
            else "projects/12345/locations/global"
        },
        {
            "metrics": []
            if issue == "quota"
            else [
                {
                    "metric": "generate_content_free_tier_requests",
                    "consumerQuotaLimits": [
                        {
                            "unit": unit,
                            "quotaBuckets": [
                                {"effectiveLimit": "20", "dimensions": {"model": FREE_MODEL}}
                            ],
                        }
                        for unit in ("1/min/{project}", "1/d/{project}")
                    ],
                }
            ]
        },
    ]
    session = MagicMock()
    session.get.side_effect = [
        SimpleNamespace(
            status_code=403 if issue == "permission" else 200,
            content=b"bounded",
            json=lambda row=row: row,
        )
        for row in responses
    ]
    wrapper = MagicMock()
    wrapper.__enter__.return_value = session
    monkeypatch.setattr("google.auth.transport.requests.AuthorizedSession", lambda _: wrapper)
    if issue == "none":
        assert verify_free_project(settings).current(settings)
        assert session.get.call_count == 4
    else:
        with pytest.raises(ProviderFailure):
            verify_free_project(settings)
    assert not session.post.called and not session.patch.called


def test_shared_provider_limits_across_requests_and_window_reset(finance_engine: Engine) -> None:
    settings = config(requests_per_day=2)
    verified = proof(settings)
    first = reserve(finance_engine, settings, verified)
    with pytest.raises(ProviderFailure, match="AI_CONCURRENCY_LIMIT"):
        reserve(finance_engine, settings, verified)
    release(finance_engine, settings.project_id, first)
    second = reserve(finance_engine, settings, verified)
    release(finance_engine, settings.project_id, second)
    with pytest.raises(ProviderFailure, match="AI_DAILY_LIMIT"):
        reserve(finance_engine, settings, verified)
    with Session(finance_engine) as session, session.begin():
        row = session.get(ProviderBudget, settings.project_id)
        assert row is not None
        row.day_start = row.minute_start = datetime.now(UTC) - timedelta(days=1)
    token = reserve(finance_engine, settings, verified)
    release(finance_engine, settings.project_id, token)
