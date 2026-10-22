import { render, screen } from "@testing-library/react";
import { ProcessingJobs } from "./processing-jobs";

const originalFetch = global.fetch;
afterEach(() => {
  global.fetch = originalFetch;
});

test("shows persisted failures, capacity and real replay guidance without a fake control", async () => {
  global.fetch = jest.fn().mockResolvedValue({
    ok: true,
    json: async () => ({
      items: [
        {
          id: "job1",
          invoice_id: "uploaded-invoice",
          state: "failed",
          stage: "failed",
          attempts: 5,
          error_code: "OBJECT_MISSING",
          retryable: true,
          next_action:
            "Admin may repair the dependency and replay with a reason.",
          next_attempt_at: null,
        },
      ],
      page_info: { has_next_page: false, end_cursor: null },
      active_count: 0,
      admission_limit: 500,
    }),
  });
  render(<ProcessingJobs />);
  expect(await screen.findByText("uploaded-invoice")).toBeInTheDocument();
  expect(screen.getByText("Failed")).toBeInTheDocument();
  expect(screen.getByText("OBJECT_MISSING")).toBeInTheDocument();
  expect(screen.getByText("5 / 5")).toBeInTheDocument();
  expect(screen.getByText(/Admin may repair/)).toBeInTheDocument();
  expect(
    screen.queryByRole("button", { name: /replay|retry/i }),
  ).not.toBeInTheDocument();
});

test("empty activity and retrying status reflect API responses", async () => {
  global.fetch = jest.fn().mockResolvedValue({
    ok: true,
    json: async () => ({
      items: [],
      page_info: { has_next_page: false, end_cursor: null },
      active_count: 0,
      admission_limit: 500,
    }),
  });
  render(<ProcessingJobs />);
  expect(
    await screen.findByText(/No invoice processing jobs/),
  ).toBeInTheDocument();
  expect(
    screen.getByText(/Captured text still requires financial/),
  ).toBeInTheDocument();
});

test("an unavailable API shows recovery feedback rather than fabricated jobs", async () => {
  global.fetch = jest.fn().mockResolvedValue({ ok: false });
  render(<ProcessingJobs />);
  expect(
    await screen.findByText(/Reconnecting automatically/),
  ).toBeInTheDocument();
  expect(screen.queryByRole("table")).not.toBeInTheDocument();
});
