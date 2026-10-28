import { defineConfig, devices } from "@playwright/test";

// The browser tests run the real backend (with its built-in test model, so no provider key is
// needed) against a real PostgreSQL, and the production build of the web app in front of it.
// Prepare the database first with e2e/prepare.sh. Nothing here talks to a model provider.
const BACKEND_PORT = 8100;
const WEB_PORT = 3100;

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [["list"]],
  use: {
    baseURL: `http://127.0.0.1:${WEB_PORT}`,
    trace: "retain-on-failure",
    ...devices["Desktop Chrome"],
    // Set this to use a Chromium that is already installed instead of downloading Playwright's.
    launchOptions: process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {},
  },
  webServer: [
    {
      command: "uv run python -m energyops.service --port 8100",
      cwd: "..",
      url: `http://127.0.0.1:${BACKEND_PORT}/health`,
      reuseExistingServer: false,
      timeout: 60_000,
      env: {
        ENERGYOPS_ENVIRONMENT: "local",
        ENERGYOPS_DATABASE_URL: "postgresql+psycopg://energyops_app:energyops_app@127.0.0.1:5433/energyops_e2e",
        ENERGYOPS_LOCAL_JWT_SECRET: "e2e-only-secret-0123456789-0123456789-0123456789",
      },
    },
    {
      command: `npm run start -- --port ${WEB_PORT}`,
      url: `http://127.0.0.1:${WEB_PORT}/login`,
      reuseExistingServer: false,
      timeout: 60_000,
      env: {
        BACKEND_URL: `http://127.0.0.1:${BACKEND_PORT}`,
        INSECURE_LOCAL_COOKIE: "1",
        NODE_ENV: "production",
      },
    },
  ],
});
