import createClient from "openapi-fetch";
import type { paths, components } from "./schema";
export * from "./graphql";
export { createGraphqlClient, GraphQLRequestError } from "./graphql-client";

export type Snapshot = components["schemas"]["DashboardSnapshot"];
export type Invoice = components["schemas"]["InvoiceRead"];
export type PurchaseOrder = components["schemas"]["PurchaseOrderRead"];
export type ProcessingJob = components["schemas"]["ProcessingJobRead"];
export type ProcessingJobPage = components["schemas"]["ProcessingJobPage"];

export function createApi(
  baseUrl: string,
  fetcher: typeof fetch = fetch,
  token?: string,
) {
  return createClient<paths>({
    baseUrl,
    fetch: fetcher,
    headers: token ? { Authorization: `Bearer ${token}` } : undefined,
  });
}

// Schema types accompany the handwritten client factory; schema.ts is generated.
export type { components } from "./schema";
