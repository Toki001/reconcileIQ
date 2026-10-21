import type { NextConfig } from "next";
import path from "node:path";

const config: NextConfig = {
  output: process.env.RECONCILE_STANDALONE === "1" ? "standalone" : undefined,
  outputFileTracingRoot: path.join(process.cwd(), "../.."),
  transpilePackages: ["@reconcileiq/client", "openapi-fetch"],
  poweredByHeader: false,
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "Referrer-Policy", value: "same-origin" },
          { key: "X-Frame-Options", value: "DENY" },
        ],
      },
    ];
  },
};

export default config;
