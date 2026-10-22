import { createApi } from "@reconcileiq/client";
import { getWebConfig } from "@/lib/config";

export const dynamic = "force-dynamic";

export async function GET(request: Request) {
  const config = getWebConfig();
  const client = createApi(
    config.API_BASE_URL,
    fetch,
    config.RECONCILE_WEB_API_TOKEN,
  );
  const query = new URL(request.url).searchParams;
  try {
    const result = await client.GET("/v1/processing-jobs", {
      params: { query: { first: 20, after: query.get("after") || undefined } },
      cache: "no-store",
      signal: AbortSignal.timeout(8000),
    });
    if (result.error || !result.data) throw new Error("unavailable");
    return Response.json(result.data, {
      headers: { "Cache-Control": "no-store" },
    });
  } catch {
    return Response.json(
      { error: "Processing status is temporarily unavailable." },
      { status: 503, headers: { "Cache-Control": "no-store" } },
    );
  }
}
