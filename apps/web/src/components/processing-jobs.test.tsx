import { act, fireEvent, render, screen } from "@testing-library/react";
import { ProcessingJobs } from "./processing-jobs";

const originalFetch = global.fetch;
afterEach(() => {
  global.fetch = originalFetch;
  jest.useRealTimers();
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

test("empty activity reflects the API response and keeps the financial boundary visible", async () => {
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

function activity(state: string, stage = state, more = false) {
  return {
    ok: true,
    json: async () => ({
      items: [
        {
          id: "job",
          invoice_id: "synthetic-poll",
          state,
          stage,
          attempts: state === "running" ? 1 : 0,
          error_code: null,
          next_action: "Inspect persisted activity.",
          next_attempt_at: null,
        },
      ],
      page_info: {
        has_next_page: more,
        end_cursor: more ? "signed+/cursor=" : null,
      },
      active_count: state === "succeeded" ? 0 : 1,
      admission_limit: 500,
    }),
  };
}

test("polling renders queued, running, retrying and successful persisted transitions", async () => {
  jest.useFakeTimers();
  global.fetch = jest
    .fn()
    .mockResolvedValueOnce(activity("queued"))
    .mockResolvedValueOnce(activity("running"))
    .mockResolvedValueOnce(activity("pending_dispatch", "retrying"))
    .mockResolvedValue(activity("succeeded"));
  render(<ProcessingJobs />);
  expect(await screen.findByText("Queued")).toBeInTheDocument();
  for (const label of ["Processing", "Retrying", "Completed"]) {
    await act(async () => {
      jest.advanceTimersByTime(3000);
    });
    expect(await screen.findByText(label)).toBeInTheDocument();
  }
  expect(
    screen.getByText("0 of 500 active job slots in use."),
  ).toBeInTheDocument();
});

test("cursor pagination encodes the server cursor and returns to the first page", async () => {
  global.fetch = jest
    .fn()
    .mockResolvedValueOnce(activity("succeeded", "completed", true))
    .mockResolvedValue(activity("failed"));
  render(<ProcessingJobs />);
  fireEvent.click(await screen.findByRole("button", { name: "Next page" }));
  expect(await screen.findByText("Failed")).toBeInTheDocument();
  expect(global.fetch).toHaveBeenLastCalledWith(
    "/api/processing?after=signed%2B%2Fcursor%3D",
    expect.anything(),
  );
  fireEvent.click(screen.getByRole("button", { name: "First page" }));
  await screen.findByText("Failed");
  expect(global.fetch).toHaveBeenLastCalledWith(
    "/api/processing",
    expect.anything(),
  );
});

test("a transient polling failure retains last-known rows and recovers without remounting", async () => {
  jest.useFakeTimers();
  global.fetch = jest
    .fn()
    .mockResolvedValueOnce(activity("running"))
    .mockRejectedValueOnce(new Error("synthetic network interruption"))
    .mockResolvedValue(activity("succeeded"));
  const view = render(<ProcessingJobs />);
  await screen.findByText("Processing");
  await act(async () => {
    jest.advanceTimersByTime(3000);
  });
  expect(screen.getByText(/Reconnecting automatically/)).toBeInTheDocument();
  expect(screen.getByText("synthetic-poll")).toBeInTheDocument();
  await act(async () => {
    jest.advanceTimersByTime(3000);
  });
  expect(await screen.findByText("Completed")).toBeInTheDocument();
  expect(
    screen.queryByText(/Reconnecting automatically/),
  ).not.toBeInTheDocument();
  const signal = (global.fetch as jest.Mock).mock.calls[0][1].signal;
  view.unmount();
  expect(signal.aborted).toBe(true);
  await act(async () => {
    jest.advanceTimersByTime(3000);
  });
  expect(global.fetch).toHaveBeenCalledTimes(3);
});

test("an unavailable API shows recovery feedback rather than fabricated jobs", async () => {
  global.fetch = jest.fn().mockResolvedValue({ ok: false });
  render(<ProcessingJobs />);
  expect(
    await screen.findByText(/Reconnecting automatically/),
  ).toBeInTheDocument();
  expect(screen.queryByRole("table")).not.toBeInTheDocument();
});
