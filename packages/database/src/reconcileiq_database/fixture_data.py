"""Fixed-seed synthetic financial ground truth and deterministic digital PDF fixtures."""

import hashlib
import json
import random
from decimal import Decimal
from pathlib import Path
from typing import Any

from reconcileiq_reconciliation.accounting import TaxMode, calculate_line
from reconcileiq_reconciliation.money import CURRENCY_EXPONENTS, to_minor

ORG = "synthetic-finance"
SEED = 20261022
SCENARIOS = (
    "exact",
    "taxed",
    "discounted",
    "tax_inclusive",
    "invalid_total",
    "price_discrepancy",
    "quantity_discrepancy",
    "partial_receipt",
    "tax_discrepancy",
    "duplicate_attempt",
)


def money_text(amount: int, currency: str) -> str:
    exponent = CURRENCY_EXPONENTS[currency]
    scale = 10**exponent
    return f"{currency} {amount // scale}" + (f".{amount % scale:0{exponent}d}" if exponent else "")


def pdf_document(invoice: dict[str, Any]) -> bytes:
    """Small valid PDF 1.4 with selectable text; no timestamps or random object IDs."""
    mode = invoice["tax_mode"]
    pages = 1 + invoice["index"] % 3
    content = [
        "SYNTHETIC FIXTURE - NO PAYMENT",
        f"Invoice {invoice['supplier_invoice_number']}",
        f"Vendor: {invoice['vendor_name']}",
        "Invoice date: 2026-10-01    Due date: 2026-11-01",
        f"Purchase order: {invoice['po_reference']}",
        f"Currency: {invoice['currency']}    Tax mode: {mode}",
    ]
    for row in invoice["lines"]:
        content.append(
            f"{row['reference']} | qty {row['quantity']} | unit "
            + money_text(row["unit_price_minor"], invoice["currency"])
            + " | gross "
            + money_text(row["line_total_minor"], invoice["currency"])
        )
    for name in ("subtotal", "discount", "tax", "amount"):
        content.append(
            name.upper() + ": " + money_text(invoice[name + "_minor"], invoice["currency"])
        )
    content += [
        "This document contains invented development data.",
        "Financial correctness requires deterministic validation.",
    ]
    objects: list[bytes] = [b"", b""]
    font_id = 3
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    page_ids = []
    for index in range(pages):
        page_id, stream_id = len(objects) + 1, len(objects) + 2
        page_ids.append(page_id)
        text = (
            content
            if index == 0
            else [
                "SYNTHETIC CONTINUATION - NO PAYMENT",
                f"Invoice {invoice['supplier_invoice_number']} - page {index + 1}",
                "Supporting goods receipt and reference details for future OCR tests.",
                f"Scenario label: {invoice['scenario']}",
                "No goods were ordered, received or paid for in the real world.",
            ]
        )
        commands = ["BT", "/F1 11 Tf", f"{48 + (invoice['index'] % 2) * 24} 780 Td", "18 TL"]
        for line in text:
            escaped = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            commands.append(f"({escaped}) Tj T*")
        commands.append("ET")
        stream = "\n".join(commands).encode("ascii")
        objects.append(
            (
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 842] "
                f"/Resources << /Font << /F1 {font_id} 0 R >> >> "
                f"/Contents {stream_id} 0 R >>"
            ).encode()
        )
        objects.append(f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"\nendstream")
    objects[0] = b"<< /Type /Catalog /Pages 2 0 R >>"
    kids = " ".join(f"{page_id} 0 R" for page_id in page_ids)
    objects[1] = f"<< /Type /Pages /Kids [{kids}] /Count {pages} >>".encode()
    result = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for index, obj in enumerate(objects, 1):
        offsets.append(len(result))
        result.extend(f"{index} 0 obj\n".encode() + obj + b"\nendobj\n")
    xref = len(result)
    result.extend(f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode())
    for offset in offsets[1:]:
        result.extend(f"{offset:010d} 00000 n \n".encode())
    result.extend(
        f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    )
    return bytes(result)


def dataset() -> dict[str, Any]:
    rng = random.Random(SEED)
    currencies = list(CURRENCY_EXPONENTS)
    vendors: list[dict[str, Any]] = [
        {
            "index": i,
            "code": f"SYN-V-{i + 1:03}",
            "name": f"Synthetic {chr(65 + i)} Development Supplies",
            "currency": currencies[i % len(currencies)],
        }
        for i in range(20)
    ]
    orders, invoices = [], []
    for p in range(100):
        vendor = vendors[p % 20]
        currency = vendor["currency"]
        mode: TaxMode = "inclusive" if p % 4 == 3 or p % 5 == 1 else "exclusive"
        rate = Decimal("0.12") if p % 4 in (1, 3) or p % 5 in (0, 1) else Decimal("0")
        lines: list[dict[str, Any]] = []
        for number in range(2):
            quantity = Decimal(rng.randint(4, 12))
            price = to_minor(Decimal(20 + p % 20 + number) + Decimal(p % 97) / 1000, currency)
            result = calculate_line(quantity, price, 0, rate, mode)
            lines.append(
                {
                    "reference": f"ITEM-{number + 1}",
                    "description": f"Synthetic component {number + 1}",
                    "quantity": format(quantity, "f"),
                    "unit_price_minor": price,
                    "line_total_minor": result.gross_minor,
                    "discount_minor": 0,
                    "tax_rate": str(rate),
                    "tax_minor": result.tax_minor,
                    "total_minor": result.total_minor,
                }
            )
        order = {
            "index": p,
            "reference": f"SYN-PO-{p + 1:04}",
            "vendor_index": p % 20,
            "currency": currency,
            "tax_mode": mode,
            "lines": lines,
            "subtotal_minor": sum(line["line_total_minor"] for line in lines),
            "tax_minor": sum(line["tax_minor"] for line in lines),
            "discount_minor": 0,
            "amount_minor": sum(line["total_minor"] for line in lines),
            "receipt_fraction": "0.25" if p % 5 == 3 else "1",
        }
        orders.append(order)
        for offset in range(2):
            index = p * 2 + offset
            scenario = SCENARIOS[index % len(SCENARIOS)]
            invoice_lines: list[dict[str, Any]] = []
            for row in lines:
                quantity = Decimal(row["quantity"]) / 2
                price, invoice_rate = row["unit_price_minor"], Decimal(row["tax_rate"])
                if scenario == "price_discrepancy":
                    price += 17
                if scenario == "quantity_discrepancy":
                    quantity = Decimal(row["quantity"]) * 2
                if scenario == "tax_discrepancy":
                    invoice_rate = Decimal("0.15")
                gross = calculate_line(quantity, price, 0, invoice_rate, mode).gross_minor
                discount = gross // 20 if scenario == "discounted" else 0
                calculated = calculate_line(quantity, price, discount, invoice_rate, mode)
                invoice_lines.append(
                    {
                        "reference": row["reference"],
                        "description": row["description"],
                        "quantity": str(quantity),
                        "unit_price_minor": price,
                        "line_total_minor": calculated.gross_minor,
                        "discount_minor": discount,
                        "tax_rate": str(invoice_rate),
                        "tax_minor": calculated.tax_minor,
                        "total_minor": calculated.total_minor,
                    }
                )
            status = {
                "price_discrepancy": "exception",
                "quantity_discrepancy": "exception",
                "partial_receipt": "needs_review",
                "invalid_total": "needs_review",
                "tax_discrepancy": "reviewed_rejected",
                "discounted": "reviewed_accepted",
                "duplicate_attempt": "duplicate",
            }.get(scenario, "matched")
            invoice = {
                "index": index,
                "supplier_invoice_number": f"SYN-{p % 20 + 1:02}-{index + 1:04}",
                "vendor_index": p % 20,
                "vendor_name": vendor["name"],
                "po_reference": order["reference"],
                "currency": currency,
                "tax_mode": mode,
                "scenario": scenario,
                "status": status,
                "lines": invoice_lines,
                "subtotal_minor": sum(row["line_total_minor"] for row in invoice_lines),
                "discount_minor": sum(row["discount_minor"] for row in invoice_lines),
                "tax_minor": sum(row["tax_minor"] for row in invoice_lines),
                "amount_minor": sum(row["total_minor"] for row in invoice_lines)
                + (1 if scenario == "invalid_total" else 0),
            }
            invoice["pdf_sha256"] = hashlib.sha256(pdf_document(invoice)).hexdigest()
            invoices.append(invoice)
    return {
        "schema": "synthetic-finance-v1",
        "seed": SEED,
        "organization_id": ORG,
        "vendors": vendors,
        "purchase_orders": orders,
        "invoices": invoices,
    }


def generate_fixtures(directory: Path, *, check: bool = False) -> None:
    data = dataset()
    expected = {"manifest.json": (json.dumps(data, sort_keys=True, indent=2) + "\n").encode()}
    expected.update(
        {f"invoice-{row['index'] + 1:04}.pdf": pdf_document(row) for row in data["invoices"]}
    )
    if check:
        if any(
            not (directory / name).exists() or (directory / name).read_bytes() != content
            for name, content in expected.items()
        ):
            raise ValueError("Synthetic fixture drift: run make fixtures")
        if {p.name for p in directory.iterdir()} != set(expected):
            raise ValueError("Unexpected files in the dedicated fixture directory")
        return
    directory.mkdir(parents=True, exist_ok=True)
    for name, content in expected.items():
        (directory / name).write_bytes(content)
