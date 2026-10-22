import "server-only";
import { createApi } from "@reconcileiq/client";
import { getWebConfig } from "./config";

export async function getSnapshot() {
  const config = getWebConfig();
  const client = createApi(
    config.API_BASE_URL,
    fetch,
    config.RECONCILE_WEB_API_TOKEN,
  );
  const { data, error } = await client.GET("/v1/demo/snapshot", {
    cache: "no-store",
    signal: AbortSignal.timeout(8000),
  });
  if (error || !data) throw new Error("Finance workspace is unavailable");
  return data;
}
