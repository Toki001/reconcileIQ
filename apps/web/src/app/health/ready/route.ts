import { createApi } from "@reconcileiq/client";
import { getWebConfig } from "@/lib/config";

export const dynamic = "force-dynamic";

export async function GET() {
  try {
    const { data, error } = await createApi(getWebConfig().API_BASE_URL).GET(
      "/health/ready",
      {
        cache: "no-store",
        signal: AbortSignal.timeout(8000),
      },
    );
    if (error || data?.status !== "ok") throw new Error("API unavailable");
    return Response.json({ status: "ok", service: "web" });
  } catch {
    return Response.json(
      { status: "unavailable", service: "web" },
      { status: 503 },
    );
  }
}
