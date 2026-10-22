from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from reconcileiq_reconciliation.money import line_total_minor, validate_header_totals

from reconcileiq_api.schemas import Currency, MoneyMinor

Identifier = Annotated[str, Field(min_length=1, max_length=32, pattern=r"^[a-zA-Z0-9_-]+$")]
Quantity = Annotated[str, Field(pattern=r"^(?:0|[1-9][0-9]{0,8})(?:\.[0-9]{1,6})?$")]
Version = Annotated[int, Field(strict=True, ge=1, le=2_147_483_647)]
Policy = Literal["two_way_exact_v1", "three_way_exact_v1"]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Output(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    @field_validator("*", mode="before")
    @classmethod
    def normalize_time(cls, value: object) -> object:
        if isinstance(value, datetime) and value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value


class UploadRequest(Input):
    filename: str = Field(min_length=5, max_length=160, pattern=r"^[^/\\\x00-\x1f]+\.[pP][dD][fF]$")
    content_type: Literal["application/pdf"]
    size_bytes: int = Field(strict=True, ge=5, le=10_485_760)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    purchase_order_id: Identifier | None = None


class UploadGrant(Output):
    invoice_id: str
    upload_url: str
    method: Literal["PUT"] = "PUT"
    required_headers: dict[str, str]
    expires_at: datetime
    max_size_bytes: int = 10_485_760


class UploadComplete(Input):
    expected_version: Version


class PurchaseOrderLine(Input):
    id: Identifier
    description: str = Field(min_length=1, max_length=200)
    quantity: Quantity
    unit_price_minor: MoneyMinor
    line_total_minor: MoneyMinor

    @model_validator(mode="after")
    def arithmetic(self) -> "PurchaseOrderLine":
        if line_total_minor(Decimal(self.quantity), self.unit_price_minor) != self.line_total_minor:
            raise ValueError(
                "Line total must equal quantity times unit price with half-up rounding"
            )
        return self


class PurchaseOrderCreate(Input):
    vendor: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=200)
    currency: Currency
    issued_on: date
    lines: list[PurchaseOrderLine] = Field(min_length=1, max_length=100)
    subtotal_minor: MoneyMinor
    tax_minor: MoneyMinor
    discount_minor: MoneyMinor
    amount_minor: MoneyMinor
    tax_mode: Literal["exclusive"]

    @model_validator(mode="after")
    def totals(self) -> "PurchaseOrderCreate":
        if len({row.id for row in self.lines}) != len(self.lines):
            raise ValueError("Purchase order line IDs must be unique")
        validate_header_totals(
            [row.line_total_minor for row in self.lines],
            self.subtotal_minor,
            self.discount_minor,
            self.tax_minor,
            self.amount_minor,
        )
        return self


class PurchaseOrderUpdate(Input):
    expected_version: Version
    description: str = Field(min_length=1, max_length=200)
    status: Literal["open", "cancelled"]


class PurchaseOrderDetail(Output):
    id: str
    vendor: str
    description: str
    currency: Currency
    amount_minor: MoneyMinor
    issued_on: date
    status: Literal["open", "received", "cancelled"]
    version: int
    created_at: datetime
    line_items: list[PurchaseOrderLine]
    subtotal_minor: MoneyMinor | None
    tax_minor: MoneyMinor | None
    discount_minor: MoneyMinor | None


class ReceiptLine(Input):
    purchase_order_line_id: Identifier
    quantity: Quantity

    @model_validator(mode="after")
    def positive(self) -> "ReceiptLine":
        if Decimal(self.quantity) <= 0:
            raise ValueError("Received quantity must be positive")
        return self


class ReceiptCreate(Input):
    purchase_order_id: Identifier
    reference: str = Field(min_length=1, max_length=80)
    received_on: date
    lines: list[ReceiptLine] = Field(min_length=1, max_length=100)


class ReceiptRead(ReceiptCreate, Output):
    id: str
    created_at: datetime


class ReconciliationStart(Input):
    invoice_id: Identifier
    expected_version: Version
    policy_id: Policy


class ReviewCreate(Input):
    reconciliation_id: Identifier
    expected_version: Version
    decision: Literal["accept", "reject"]
    reason: str = Field(min_length=10, max_length=1000)


class ReviewRead(Output):
    id: str
    invoice_id: str
    reconciliation_id: str
    invoice_version: int
    decision: Literal["accept", "reject"]
    reason: str
    actor: str
    created_at: datetime


class ReplayRequest(Input):
    expected_version: Version
    reason: str = Field(min_length=10, max_length=1000)


class JobRead(Output):
    id: str
    invoice_id: str | None
    kind: Literal["extraction", "reconciliation", "supplier_webhook"]
    state: Literal["pending_dispatch", "queued", "running", "succeeded", "failed", "cancelled"]
    version: int
    attempts: int
    error_code: str | None
    retryable: bool
    replay_of: str | None
    created_at: datetime
    updated_at: datetime
    next_action: str = "Poll this resource for current processing state and next action."


class JobAccepted(Output):
    job_id: str
    invoice_id: str | None = None
    reconciliation_id: str | None = None
    status: Literal["pending_dispatch"] = "pending_dispatch"
    status_url: str


class InvoiceDetail(Output):
    id: str
    vendor: str | None
    description: str
    currency: Currency | None
    amount_minor: MoneyMinor | None
    due_on: date | None
    status: Literal[
        "upload_requested",
        "pending_extraction",
        "extracted",
        "matched",
        "exception",
        "needs_review",
        "duplicate",
        "reviewed_accepted",
        "reviewed_rejected",
    ]
    purchase_order_id: str | None
    version: int
    synthetic: bool
    created_at: datetime


class PageInfo(Output):
    has_next_page: bool
    end_cursor: str | None


class InvoicePage(Output):
    items: list[InvoiceDetail]
    page_info: PageInfo


class ProcessingJobRead(JobRead):
    stage: str
    next_attempt_at: datetime | None


class ProcessingJobPage(Output):
    items: list[ProcessingJobRead]
    page_info: PageInfo
    active_count: int
    admission_limit: int


class PurchaseOrderPage(Output):
    items: list[PurchaseOrderDetail]
    page_info: PageInfo


class SupplierNotice(Input):
    event_id: str = Field(min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9_-]+$")
    event_type: Literal["invoice.available"]
    supplier_invoice_number: str = Field(min_length=1, max_length=80)
    document_reference: str = Field(min_length=1, max_length=256)
