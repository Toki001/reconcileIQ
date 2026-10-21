import type { Snapshot } from "@reconcileiq/client";

export const fixture: Snapshot = {
  synthetic: true,
  as_of: "2026-10-21",
  invoices: [
    {
      id: "INV-001",
      vendor: "Northstar Studio",
      description: "Design",
      currency: "USD",
      amount_minor: 480000,
      due_on: "2026-10-23",
      status: "matched",
      purchase_order_id: "PO-001",
      exception_code: null,
      exception_detail: null,
    },
    {
      id: "INV-002",
      vendor: "Acme Office",
      description: "Chairs",
      currency: "USD",
      amount_minor: 186000,
      due_on: "2026-10-24",
      status: "exception",
      purchase_order_id: "PO-002",
      exception_code: "QUANTITY_MISMATCH",
      exception_detail: "Two units not received.",
    },
  ],
  purchase_orders: [
    {
      id: "PO-001",
      vendor: "Northstar Studio",
      description: "Design",
      currency: "USD",
      amount_minor: 480000,
      issued_on: "2026-10-01",
      status: "received",
    },
    {
      id: "PO-002",
      vendor: "Acme Office",
      description: "Chairs",
      currency: "USD",
      amount_minor: 186000,
      issued_on: "2026-10-01",
      status: "open",
    },
  ],
  audit_events: [
    {
      id: "event-001",
      entity_id: "INV-002",
      actor: "local-seed",
      action: "demo_record_created",
      detail: "Synthetic fixture created.",
      occurred_at: "2026-10-21T09:00:00Z",
    },
  ],
};
