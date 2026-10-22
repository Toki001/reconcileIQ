import { cookies } from "next/headers";
import { getWebConfig } from "@/lib/config";
import { boundedBody, sameOrigin, sealSession } from "@/lib/review-session";
export const runtime = "nodejs";
export async function POST(request: Request) {
  if (
    !sameOrigin(request) ||
    !request.headers.get("content-type")?.startsWith("application/json")
  )
    return Response.json(
      { detail: "Same-origin JSON required." },
      { status: 403 },
    );
  try {
    const { token } = JSON.parse(
      new TextDecoder().decode(await boundedBody(request, 1024)),
    );
    if (typeof token !== "string" || !/^[\x21-\x7e]{32,256}$/.test(token))
      return Response.json(
        { detail: "A valid local API key is required." },
        { status: 401 },
      );
    const response = await fetch(`${getWebConfig().API_BASE_URL}/v1/me`, {
      headers: { Authorization: `Bearer ${token}` },
      cache: "no-store",
      signal: AbortSignal.timeout(10000),
    });
    if (!response.ok)
      return Response.json(
        { detail: "Credential verification failed." },
        { status: response.status === 401 ? 401 : 503 },
      );
    const identity = await response.json();
    const session = sealSession(token);
    (await cookies()).set("reconcile-review", session, {
      httpOnly: true,
      sameSite: "strict",
      secure: new URL(request.url).protocol === "https:",
      path: "/api",
      maxAge: 1800,
    });
    return Response.json(identity, {
      headers: { "Cache-Control": "no-store" },
    });
  } catch {
    return Response.json(
      { detail: "Review session unavailable. Check local configuration." },
      { status: 503 },
    );
  }
}
export async function DELETE(request: Request) {
  if (!sameOrigin(request)) return new Response(null, { status: 403 });
  (await cookies()).delete({ name: "reconcile-review", path: "/api" });
  return new Response(null, { status: 204 });
}
