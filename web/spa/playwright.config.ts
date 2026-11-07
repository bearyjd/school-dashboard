import { defineConfig } from '@playwright/test'

const PORT = Number(process.env.E2E_PORT ?? 5099)

/**
 * Specs run against scripts/spa-qa-server.py (synthetic data, real Flask app + built SPA).
 * Build first: `npm run build`. Run: `npm run e2e`. Screenshots go to $E2E_SHOTS (default test-results/shots).
 */
export default defineConfig({
  testDir: './e2e',
  timeout: 60_000,
  fullyParallel: false,
  workers: 1,
  reporter: [['list']],
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    serviceWorkers: 'block',
  },
  webServer: {
    command: `python3 ../../scripts/spa-qa-server.py --port ${PORT}`,
    url: `http://127.0.0.1:${PORT}/app/`,
    stdout: 'ignore',
    reuseExistingServer: !process.env.CI,
    timeout: 60_000,
  },
})
