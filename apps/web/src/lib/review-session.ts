import "server-only";
import { createCipheriv, createDecipheriv, randomBytes } from "node:crypto";

function key(): Buffer {
  const value = process.env.RECONCILE_WEB_SESSION_SECRET ?? "";
  if (!/^[a-f0-9]{64}$/.test(value))
    throw new Error("Review session configuration unavailable");
  return Buffer.from(value, "hex");
}
export function sealSession(token: string, at = Date.now()): string {
  const iv = randomBytes(12);
  const cipher = createCipheriv("aes-256-gcm", key(), iv);
  const body = Buffer.concat([
    cipher.update(JSON.stringify({ token, expires: at + 1800000 })),
    cipher.final(),
  ]);
  return Buffer.concat([iv, cipher.getAuthTag(), body]).toString("base64url");
}
export function openSession(value: string, at = Date.now()): string | null {
  try {
    if (value.length > 1000) return null;
    const data = Buffer.from(value, "base64url");
    const decipher = createDecipheriv(
      "aes-256-gcm",
      key(),
      data.subarray(0, 12),
    );
    decipher.setAuthTag(data.subarray(12, 28));
    const body = JSON.parse(
      Buffer.concat([
        decipher.update(data.subarray(28)),
        decipher.final(),
      ]).toString(),
    );
    return typeof body.token === "string" &&
      body.token.length >= 32 &&
      body.token.length <= 256 &&
      body.expires > at &&
      body.expires <= at + 1800000
      ? body.token
      : null;
  } catch {
    return null;
  }
}
export function sameOrigin(request: Request): boolean {
  try {
    const supplied = request.headers.get("origin");
    if (!supplied) return false;
    const origin = new URL(supplied);
    const target = new URL(request.url);
    // Next's internal URL uses port 3000 behind Compose's published port. The
    // HTTP Host retains the browser authority; browsers cannot forge Host.
    return (
      origin.host === (request.headers.get("host") ?? target.host) &&
      origin.protocol === target.protocol &&
      !origin.username &&
      !origin.password
    );
  } catch {
    return false;
  }
}
export async function boundedBody(
  request: Request,
  limit: number,
): Promise<Uint8Array> {
  const reader = request.body?.getReader();
  if (!reader) return new Uint8Array();
  const parts: Uint8Array[] = [];
  let size = 0;
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    size += value.length;
    if (size > limit) {
      await reader.cancel();
      throw new Error("Request too large");
    }
    parts.push(value);
  }
  return new Uint8Array(Buffer.concat(parts));
}
