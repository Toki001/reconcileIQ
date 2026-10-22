"""Reviewed additions for bounded evidence and human review; money remains untrusted."""

from datetime import datetime
from typing import Any, Literal

from pydantic import Field
from reconcileiq_ai.invoice import InvoiceCandidate

from reconcileiq_api.auth import Role
from reconcileiq_api.contracts import Input, InvoiceDetail, Output, Version


class IdentityRead(Output):
    organization_id: str
    actor: str
    role: Role


class ProviderStatus(Output):
    available: bool
    model: str
    code: str
    checked_at: datetime | None = None
    synthetic_only: Literal[True] = True


class StructuredStart(Input):
    expected_version: Version
    synthetic_authorized: Literal[True]


class CorrectionCreate(Input):
    expected_version: Version
    source_version_id: str | None = Field(default=None, max_length=32)
    reason: str = Field(min_length=10, max_length=1000)
    candidate: InvoiceCandidate


class CandidateRead(Output):
    id: str
    number: int
    origin: str
    created_at: datetime
    model: str | None
    prompt_version: str | None
    candidate: InvoiceCandidate


class DifferenceRead(Output):
    code: str
    field: str
    expected: str
    observed: str


class ResultRead(Output):
    reconciliation_id: str
    extraction_version_id: str
    invoice_version: int
    policy_id: str
    outcome: Literal[
        "matched", "unmatched", "discrepancy", "duplicate", "needs_review", "extraction_failed"
    ]
    differences: list[DifferenceRead]
    # Immutable JSON snapshot; decimal quantities are strings, amounts are minor-unit integers.
    context: dict[str, Any]


class TimelineRead(Output):
    id: str
    actor: str
    action: str
    occurred_at: datetime
    detail: str


class WorkspaceRead(Output):
    invoice: InvoiceDetail
    document_available: bool
    candidates: list[CandidateRead]
    result: ResultRead | None
    current_context: dict[str, Any] | None
    timeline: list[TimelineRead]
    history_truncated: bool
