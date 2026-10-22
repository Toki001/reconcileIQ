/** @jest-environment node */
import {
  AuditTimelineDocument,
  createGraphqlClient,
  GraphQLRequestError,
} from "@reconcileiq/client";

const variables = { entityId: "INV-1042", first: 10, after: null };

test("typed GraphQL operation sends variables and bearer identity", async () => {
  const data = {
    auditTimeline: {
      edges: [],
      pageInfo: { hasNextPage: false, endCursor: null },
    },
  };
  const fetcher = jest
    .fn<ReturnType<typeof fetch>, Parameters<typeof fetch>>()
    .mockResolvedValue(Response.json({ data }));
  const result = await createGraphqlClient(
    "http://api.local/",
    "test-key",
    fetcher,
  )(AuditTimelineDocument, variables);
  expect(result).toEqual(data);
  const [url, options] = fetcher.mock.calls[0];
  expect(url).toBe("http://api.local/graphql");
  expect(options?.headers).toMatchObject({ Authorization: "Bearer test-key" });
  expect(JSON.parse(options?.body as string).variables).toEqual(variables);
  expect(options?.cache).toBe("no-store");
});

test("GraphQL errors preserve explicit codes and reject partial results", async () => {
  const fetcher = jest
    .fn<ReturnType<typeof fetch>, Parameters<typeof fetch>>()
    .mockResolvedValue(
      Response.json({
        data: { auditTimeline: null },
        errors: [{ extensions: { code: "INVALID_CURSOR" } }],
      }),
    );
  const execute = createGraphqlClient("http://api.local", "test-key", fetcher);
  await expect(execute(AuditTimelineDocument, variables)).rejects.toMatchObject<
    Partial<GraphQLRequestError>
  >({ codes: ["INVALID_CURSOR"] });
});

test("GraphQL authentication failures remain transport failures", async () => {
  const fetcher = jest
    .fn<ReturnType<typeof fetch>, Parameters<typeof fetch>>()
    .mockResolvedValue(
      Response.json({ code: "UNAUTHENTICATED" }, { status: 401 }),
    );
  await expect(
    createGraphqlClient(
      "http://api.local",
      "test-key",
      fetcher,
    )(AuditTimelineDocument, variables),
  ).rejects.toThrow("GraphQL transport failed (401)");
});
