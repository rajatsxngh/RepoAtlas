import path from "node:path";
import { defineConfig, devices, type PlaywrightTestConfig } from "@playwright/test";

/**
 * End-to-end tests.
 *
 * - `e2e/app/`: the web app against the real API and a worker over local fixture repositories
 *   (`backend/tests/e2e/stack.py`; needs Postgres, like the backend's integration tests).
 * - `e2e/mock/`: a build that serves fixtures from memory (`NEXT_PUBLIC_API_MOCK=1`), including
 *   the 10,000-node stress test.
 *
 * `E2E_MOCK_ONLY=1 npx playwright test --project mock` runs the mock specs without the API.
 */

const CI = Boolean(process.env.CI);
const MOCK_ONLY = process.env.E2E_MOCK_ONLY === "1";
export const API_PORT = 8765;
export const WEB_PORT = 3100;
export const MOCK_WEB_PORT = 3101;

const apiServers: NonNullable<PlaywrightTestConfig["webServer"]> = [
  {
    name: "api and worker (fixture repos)",
    command: `uv run python -m tests.e2e.stack --port ${API_PORT} --web-origin http://127.0.0.1:${WEB_PORT}`,
    cwd: path.join(__dirname, "..", "backend"),
    url: `http://127.0.0.1:${API_PORT}/api/v1/ready`,
    // Always a fresh database, so builds start from scratch.
    reuseExistingServer: false,
    timeout: 180_000,
    stdout: "ignore",
    stderr: "pipe",
  },
  {
    name: "web (real API)",
    command: `npx next build && npx next start --hostname 127.0.0.1 --port ${WEB_PORT}`,
    url: `http://127.0.0.1:${WEB_PORT}`,
    env: {
      NEXT_PUBLIC_API_URL: `http://127.0.0.1:${API_PORT}`,
      NEXT_DIST_DIR: ".next-e2e",
    },
    reuseExistingServer: !CI,
    timeout: 300_000,
    stdout: "ignore",
    stderr: "pipe",
  },
];

const mockServer: NonNullable<PlaywrightTestConfig["webServer"]> = [
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
];

export default defineConfig({
  testDir: "e2e",
  timeout: 90_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1,
  retries: CI ? 1 : 0,
  forbidOnly: CI,
  reporter: CI ? [["github"], ["html", { open: "never" }], ["list"]] : [["list"]],
  use: {
    ...devices["Desktop Chrome"],
    viewport: { width: 1440, height: 900 },
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    ...(MOCK_ONLY
      ? []
      : [{ name: "app", testDir: "e2e/app", use: { baseURL: `http://127.0.0.1:${WEB_PORT}` } }]),
    { name: "mock", testDir: "e2e/mock", use: { baseURL: `http://127.0.0.1:${MOCK_WEB_PORT}` } },
  ],
  webServer: MOCK_ONLY ? mockServer : [...apiServers, ...mockServer],
});
