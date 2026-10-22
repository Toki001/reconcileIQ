"""Structured candidate evidence. Validation never establishes financial correctness."""

import re
from datetime import date
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from reconcileiq_reconciliation.money import CURRENCY_EXPONENTS

Minor = Annotated[int, Field(ge=0, le=9_007_199_254_740_991)]
DecimalText = Annotated[str, Field(pattern=r"^(?:0|[1-9][0-9]{0,8})(?:\.[0-9]{1,6})?$")]
Currency = Literal["USD", "EUR", "PHP", "JPY", "KWD"]
TaxMode = Literal["exclusive", "inclusive"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Evidence[Value](StrictModel):
    value: Value | None
    status: Literal["observed", "missing", "inferred", "human"]
    pages: list[Annotated[int, Field(ge=1, le=20)]] = Field(max_length=20)
    quote: str | None = Field(max_length=1000)

    @model_validator(mode="after")
    def consistent(self) -> "Evidence[Value]":
        if self.value is None:
            if self.status != "missing" or self.pages or self.quote is not None:
                raise ValueError("Missing fields require null value/quote and empty pages")
        elif self.status == "missing":
            raise ValueError("Missing fields cannot contain a value")
        elif self.status == "observed" and (not self.pages or not self.quote):
            raise ValueError("Observed values require page and quoted evidence")
        if len(set(self.pages)) != len(self.pages):
            raise ValueError("Source pages must be unique")
        return self


Text = Annotated[str, Field(min_length=1, max_length=200)]


class CandidateLine(StrictModel):
    reference: Evidence[Text]
    description: Evidence[Text]
    quantity: Evidence[DecimalText]
    unit_price_minor: Evidence[Minor]
    gross_minor: Evidence[Minor]
    discount_minor: Evidence[Minor]
    tax_rate: Evidence[DecimalText]
    tax_minor: Evidence[Minor]
    total_minor: Evidence[Minor]

    @model_validator(mode="after")
    def decimal_bounds(self) -> "CandidateLine":
        if self.quantity.value is not None and Decimal(self.quantity.value) <= 0:
            raise ValueError("Quantity must be positive")
        if self.tax_rate.value is not None and Decimal(self.tax_rate.value) > 1:
            raise ValueError("Tax rate must be between zero and one")
        return self


class InvoiceCandidate(StrictModel):
    invoice_number: Evidence[Annotated[str, Field(min_length=1, max_length=80)]]
    vendor_name: Evidence[Annotated[str, Field(min_length=1, max_length=120)]]
    vendor_identifier: Evidence[Text]
    invoice_date: Evidence[Annotated[str, Field(pattern=r"^\d{4}-\d{2}-\d{2}$")]]
    due_date: Evidence[Annotated[str, Field(pattern=r"^\d{4}-\d{2}-\d{2}$")]]
    purchase_order_reference: Evidence[Text]
    currency: Evidence[Currency]
    tax_mode: Evidence[TaxMode]
    subtotal_minor: Evidence[Minor]
    discount_minor: Evidence[Minor]
    tax_minor: Evidence[Minor]
    total_minor: Evidence[Minor]
    lines: list[CandidateLine] = Field(max_length=100)

    @model_validator(mode="after")
    def valid_dates(self) -> "InvoiceCandidate":
        for field in (self.invoice_date, self.due_date):
            if field.value is not None:
                date.fromisoformat(field.value)
        return self


def evidence_fields(candidate: InvoiceCandidate) -> list[tuple[str, Evidence[object]]]:
    fields: list[tuple[str, Evidence[object]]] = []
    for name in InvoiceCandidate.model_fields:
        if name != "lines":
            fields.append((name, getattr(candidate, name)))
    for index, line in enumerate(candidate.lines):
        fields.extend(
            (f"lines[{index}].{name}", getattr(line, name)) for name in CandidateLine.model_fields
        )
    return fields


def validate_sources(candidate: InvoiceCandidate, pages: dict[int, str], *, provider: bool) -> None:
    for path, field in evidence_fields(candidate):
        if provider and field.status == "human":
            raise ValueError("A provider cannot claim human provenance")
        if any(page not in pages for page in field.pages):
            raise ValueError("Source page does not exist")
        if field.status == "observed" and not any(
            field.quote is not None and field.quote in pages[page] for page in field.pages
        ):
            raise ValueError("Quoted evidence is absent from its source pages")

        if field.status == "observed" and field.quote is not None:
            quote = field.quote.casefold()
            value = field.value
            if isinstance(value, int):
                exponent = CURRENCY_EXPONENTS.get(candidate.currency.value or "", 0)
                displayed = format(Decimal(value).scaleb(-exponent), f".{exponent}f")
                # Explicit integer minor-unit labels and printed major-unit amounts are accepted.
                options = {str(value), displayed}
                tokens = set(re.findall(r"[0-9]+(?:\.[0-9]+)?", quote.replace(",", "")))
                supported = bool(options & tokens)
            elif path.endswith(("quantity", "tax_rate")) and isinstance(value, str):
                numbers = re.findall(r"[0-9]+(?:\.[0-9]+)?%?", quote)
                supported = any(
                    Decimal(number.rstrip("%")) / (100 if number.endswith("%") else 1)
                    == Decimal(value)
                    for number in numbers
                )
            else:
                supported = str(value).casefold() in quote
            if not supported:
                raise ValueError("Observed value is not supported by its source quote")
