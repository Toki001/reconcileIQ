/** @jest-environment node */
import { createApi } from "@reconcileiq/client";
import { fixture } from "@/test/fixture";

test("generated client calls the implemented contract path", async () => {
  const fetcher = jest
    .fn<ReturnType<typeof fetch>, Parameters<typeof fetch>>()
    .mockResolvedValue(Response.json(fixture));
  const { data, error } = await createApi("http://api.local", fetcher).GET(
    "/v1/demo/snapshot",
  );
  expect(error).toBeUndefined();
  expect(data?.invoices[0].amount_minor).toBe(480000);
  expect((fetcher.mock.calls[0][0] as Request).url).toBe(
    "http://api.local/v1/demo/snapshot",
  );
});

test("typed dependency failures stay failures", async () => {
  const fetcher = jest
    .fn<ReturnType<typeof fetch>, Parameters<typeof fetch>>()
    .mockResolvedValue(
      Response.json(
        { code: "DATABASE_UNAVAILABLE", detail: "Unavailable" },
        { status: 503 },
      ),
    );
  const { data, error } = await createApi("http://api.local", fetcher).GET(
    "/v1/demo/snapshot",
  );
  expect(data).toBeUndefined();
  expect(error?.code).toBe("DATABASE_UNAVAILABLE");
});
