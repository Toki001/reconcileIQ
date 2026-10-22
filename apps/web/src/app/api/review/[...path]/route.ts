import { cookies } from "next/headers";
import { getWebConfig } from "@/lib/config";
import { boundedBody, openSession, sameOrigin } from "@/lib/review-session";
export const runtime = "nodejs";
const routes = [
  /^GET v1\/(me|ai\/status|invoices)$/,
  /^POST v1\/(ai\/verify|invoices\/upload-requests|reconciliations)$/,
  /^GET v1\/invoices\/[a-zA-Z0-9_-]{1,32}\/(workspace|document)$/,
  /^GET v1\/invoices\/[a-zA-Z0-9_-]{1,32}\/pages\/[0-9]{1,2}$/,
  /^GET v1\/jobs\/[a-zA-Z0-9_-]{1,32}$/,
  /^PUT v1\/invoices\/[a-zA-Z0-9_-]{1,32}\/document$/,
  /^POST v1\/invoices\/[a-zA-Z0-9_-]{1,32}\/(corrections|structured-extractions|review-decisions|upload-complete)$/,
];
async function proxy(
  request: Request,
  context: { params: Promise<{ path: string[] }> },
) {
  const path = (await context.params).path.join("/");
  if (!routes.some((route) => route.test(`${request.method} ${path}`)))
    return new Response(null, { status: 404 });
  const write = request.method !== "GET";
  const binary = request.method === "PUT";
  if (
    write &&
    (!sameOrigin(request) ||
      !(request.headers.get("content-type") ?? "").startsWith(
        binary ? "application/pdf" : "application/json",
      ))
  )
    return Response.json(
      { detail: "Same-origin request required." },
      { status: 403 },
    );
  const token = openSession(
    (await cookies()).get("reconcile-review")?.value ?? "",
  );
  if (!token)
    return Response.json(
      { detail: "Sign in with your local API key." },
      { status: 401 },
    );
  try {
    const headers = new Headers({ Authorization: `Bearer ${token}` });
    if (write)
      headers.set(
        "Content-Type",
        binary ? "application/pdf" : "application/json",
      );
    const idempotency = request.headers.get("Idempotency-Key");
    if (idempotency) headers.set("Idempotency-Key", idempotency);
    const body = write
      ? Buffer.from(await boundedBody(request, binary ? 10485760 : 131072))
      : undefined;
    const response = await fetch(
      `${getWebConfig().API_BASE_URL}/${path}${new URL(request.url).search}`,
      {
        method: request.method,
        headers,
        body,
        cache: "no-store",
        signal: AbortSignal.timeout(30000),
      },
    );
    const result = new Uint8Array(await response.arrayBuffer());
    if (result.length > 10485760) throw new Error("Response exceeds limit");
    return new Response(response.status === 204 ? null : result, {
      status: response.status,
      headers: {
        "Content-Type":
          response.headers.get("Content-Type") ?? "application/json",
        "Cache-Control": "no-store",
        "X-Document-Pages": response.headers.get("X-Document-Pages") ?? "1",
        "X-Content-Type-Options": "nosniff",
        ...(path.endsWith("/document")
          ? { "Content-Security-Policy": "sandbox; default-src 'none'" }
          : {}),
      },
    });
  } catch {
    return Response.json(
      { detail: "Backend unavailable; refresh state before retrying." },
      { status: 503 },
    );
  }
}
export { proxy as GET, proxy as POST, proxy as PUT };
