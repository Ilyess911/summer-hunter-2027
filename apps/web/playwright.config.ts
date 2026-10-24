import { defineConfig, devices } from "@playwright/test"

// The suite runs against a throwaway seeded API (e2e/server.py), never the live database.
const API = 8820
const WEB = 3020

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: "list",
  use: { baseURL: `http://127.0.0.1:${WEB}`, trace: "retain-on-failure", ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 } },
  webServer: [
    { command: `cd ../.. && .venv/bin/python apps/web/e2e/server.py ${API}`, url: `http://127.0.0.1:${API}/api/health`, reuseExistingServer: false, timeout: 60_000 },
    { command: `pnpm next dev --port ${WEB} --hostname 127.0.0.1`, url: `http://127.0.0.1:${WEB}/login`, reuseExistingServer: false,
      timeout: 120_000, env: { SH_API_URL: `http://127.0.0.1:${API}` } },
  ],
})
