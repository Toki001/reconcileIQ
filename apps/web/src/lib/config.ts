import "server-only";
import { z } from "zod";

const webConfig = z.object({
  API_BASE_URL: z
    .url()
    .refine(
      (value) => ["http:", "https:"].includes(new URL(value).protocol),
      "API_BASE_URL must use HTTP or HTTPS",
    )
    .default("http://127.0.0.1:8000"),
});

export function getWebConfig() {
  return webConfig.parse({ API_BASE_URL: process.env.API_BASE_URL });
}
