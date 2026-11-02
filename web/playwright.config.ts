import { defineConfig, devices } from "@playwright/test";

// BASE_URL points at a running stack (local-test uses ops/compose.test.yml).
// Without it, Playwright starts the API and a production build of the web app itself.
const baseURL = process.env.BASE_URL ?? "http://127.0.0.1:4173";
const ci = !!process.env.CI;
const ADMIN_SPEC = /admin\.spec\.ts$/;

export default defineConfig({
  testDir: "e2e",
  forbidOnly: ci,
  retries: ci ? 1 : 0,
  // CI runs specs one at a time on its smaller runners; locally they run in parallel.
  workers: ci ? 1 : undefined,
  reporter: ci ? [["list"], ["html", { open: "never" }]] : "list",
  use: {
    baseURL,
    trace: ci ? "on" : "retain-on-failure",
    screenshot: "on",
  },
  // The admin spec changes catalog inputs and its undo is global, so its two browser runs
  // must not overlap: the webkit run waits for the chromium one. Every other spec runs in
  // parallel with them (TV screens must keep working while someone edits on the phone).
  projects: [
    {
      name: "chromium",
      testIgnore: ADMIN_SPEC,
      use: { ...devices["Desktop Chrome"], viewport: { width: 1920, height: 1080 } },
    },
    {
      name: "webkit",
      testIgnore: ADMIN_SPEC,
      use: { ...devices["Desktop Safari"], viewport: { width: 1920, height: 1080 } },
    },
    { name: "admin-chromium", testMatch: ADMIN_SPEC, use: { ...devices["Desktop Chrome"] } },
    {
      name: "admin-webkit",
      testMatch: ADMIN_SPEC,
      dependencies: ["admin-chromium"],
      use: { ...devices["Desktop Safari"] },
    },
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
