import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "tests/browser",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: "list",
  use: {
    baseURL: process.env.PLAYWRIGHT_BASE_URL || "http://127.0.0.1:3100",
    channel: process.env.PLAYWRIGHT_CHANNEL || "chromium",
    viewport: { width: 1440, height: 1000 },
    trace: "retain-on-failure",
  },
});
