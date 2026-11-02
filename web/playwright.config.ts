import { defineConfig, devices } from "@playwright/test";

// BASE_URL points at a running stack (local-test uses ops/compose.test.yml).
// Without it, Playwright starts the API and a production build of the web app itself.
const baseURL = process.env.BASE_URL ?? "http://127.0.0.1:4173";
const ci = !!process.env.CI;

export default defineConfig({
  testDir: "e2e",
  forbidOnly: ci,
  retries: ci ? 1 : 0,
  // One API serves every spec, and the admin spec changes its inputs (undo reverts the last
  // write, whoever made it), so CI runs specs one at a time.
  workers: ci ? 1 : undefined,
  reporter: ci ? [["list"], ["html", { open: "never" }]] : "list",
  use: {
    baseURL,
    trace: ci ? "on" : "retain-on-failure",
    screenshot: "on",
  },
  projects: [
    { name: "chromium", use: { ...devices["Desktop Chrome"], viewport: { width: 1920, height: 1080 } } },
    { name: "webkit", use: { ...devices["Desktop Safari"], viewport: { width: 1920, height: 1080 } } },
  ],
  webServer: process.env.BASE_URL
    ? undefined
    : [
        {
          // The API on the demo database (tools/e2e-api.sh seeds it from the demo feeds).
          command: "../tools/e2e-api.sh",
          url: "http://127.0.0.1:8000/health",
          timeout: 300_000,
          reuseExistingServer: !ci,
        },
        {
          command: "npm run build && npm run preview -- --host 127.0.0.1 --port 4173 --strictPort",
          url: baseURL,
          reuseExistingServer: !ci,
        },
      ],
});
