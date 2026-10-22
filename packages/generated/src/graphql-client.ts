import { print } from "graphql";
import type { TypedDocumentNode } from "@graphql-typed-document-node/core";

export class GraphQLRequestError extends Error {
  constructor(public readonly codes: string[]) {
    super("GraphQL operation failed");
  }
}

export function createGraphqlClient(
  baseUrl: string,
  token: string,
  fetcher: typeof fetch = fetch,
) {
  return async function execute<TResult, TVariables>(
    document: TypedDocumentNode<TResult, TVariables>,
    variables: TVariables,
  ): Promise<TResult> {
    const response = await fetcher(`${baseUrl.replace(/\/$/, "")}/graphql`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${token}`,
      },
      body: JSON.stringify({ query: print(document), variables }),
      cache: "no-store",
      signal: AbortSignal.timeout(10000),
    });
    if (!response.ok)
      throw new Error(`GraphQL transport failed (${response.status})`);
    const result = (await response.json()) as {
      data?: TResult;
      errors?: { extensions?: { code?: string } }[];
    };
    if (result.errors?.length)
      throw new GraphQLRequestError(
        result.errors.map((error) => error.extensions?.code || "GRAPHQL_ERROR"),
      );
    if (result.data === undefined) throw new Error("GraphQL returned no data");
    return result.data;
  };
}
