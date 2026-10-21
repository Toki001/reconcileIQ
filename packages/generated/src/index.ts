import createClient from "openapi-fetch";
import type { paths, components } from "./schema";

export type Snapshot = components["schemas"]["DashboardSnapshot"];
export type Invoice = components["schemas"]["InvoiceRead"];
export type PurchaseOrder = components["schemas"]["PurchaseOrderRead"];

export function createApi(baseUrl: string, fetcher: typeof fetch = fetch) {
  return createClient<paths>({ baseUrl, fetch: fetcher });
}
