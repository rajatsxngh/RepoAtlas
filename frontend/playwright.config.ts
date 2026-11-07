import { defineConfig, devices } from "@playwright/test";

/**
 * End-to-end tests. Mock-mode specs (`e2e/mock/`) run against a build that serves fixtures from
 * memory (`NEXT_PUBLIC_API_MOCK=1`); each project gets its own build directory and port.
 */

const CI = Boolean(process.env.CI);
export const MOCK_WEB_PORT = 3101;

export default defineConfig({
  testDir: "e2e",
  timeout: 60_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  workers: 1,
  retries: CI ? 1 : 0,
  forbidOnly: CI,
  reporter: CI ? [["github"], ["html", { open: "never" }]] : [["list"]],
  use: {
    ...devices["Desktop Chrome"],
    viewport: { width: 1440, height: 900 },
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    {
      name: "mock",
      testDir: "e2e/mock",
      use: { baseURL: `http://127.0.0.1:${MOCK_WEB_PORT}` },
    },
  ],
  webServer: [
    {
      name: "web (mock mode)",
      command: `npx next build && npx next start --hostname 127.0.0.1 --port ${MOCK_WEB_PORT}`,
      url: `http://127.0.0.1:${MOCK_WEB_PORT}`,
      env: { NEXT_PUBLIC_API_MOCK: "1", NEXT_DIST_DIR: ".next-e2e-mock" },
      reuseExistingServer: !CI,
      timeout: 300_000,
      stdout: "ignore",
      stderr: "pipe",
    },
  ],
});
