import { defineConfig } from "@playwright/test";

// End-to-end tests drive the real till in a real browser against a real API and
// database: the Docker stack in CI, or local servers. They share one venue's data, so
// they run in order on one worker.
export default defineConfig({
  testDir: "e2e",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 120_000,
  expect: { timeout: 15_000 },
  reporter: [["list"], ["html", { open: "never", outputFolder: "e2e-report" }]],
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:3000",
    // Locally, an installed browser ("msedge" or "chrome"); in CI, Playwright's Chromium.
    channel: process.env.PW_CHANNEL || undefined,
    viewport: { width: 1280, height: 800 },
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
});
