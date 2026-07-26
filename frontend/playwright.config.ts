import { defineConfig, devices } from '@playwright/test'

// Browser-emulator (e2e) tests: they render the REAL built frontend in Chromium and drive it like a
// user, with the backend API mocked via page.route (see e2e/helpers.ts). This is the robust net that
// proves a feature (e.g. a filter control) actually renders and works end-to-end.
export default defineConfig({
  testDir: './e2e',
  timeout: 30_000,
  expect: { timeout: 7_000 },
  fullyParallel: true,
  reporter: [['list']],
  use: { baseURL: 'http://localhost:5173', trace: 'on-first-retry' },
  webServer: {
    command: 'npm run dev',
    url: 'http://localhost:5173',
    reuseExistingServer: true,
    timeout: 60_000,
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
})
