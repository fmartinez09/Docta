import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  timeout: 90000,
  expect: { timeout: 20000 },
  reporter: "line",
  outputDir: process.env.DOCTA_E2E_OUTPUT ?? "test-results",
  use: {
    baseURL: process.env.DOCTA_WEB_ORIGIN,
    browserName: "chromium",
    channel: process.env.DOCTA_E2E_BROWSER_CHANNEL || undefined,
    viewport: { width: 1440, height: 1000 },
    screenshot: "only-on-failure",
  },
});
