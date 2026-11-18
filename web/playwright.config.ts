import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './e2e',
  timeout: 30000,
  use: {
    baseURL: 'http://localhost:4173',
    viewport: { width: 375, height: 667 },
    channel: 'chrome',
    headless: true,
  },
  webServer: {
    command: 'npx vite --host localhost --port 4173',
    url: 'http://localhost:4173',
    reuseExistingServer: !process.env.CI,
    env: {
      VITE_USE_MOCK_API: 'true',
      VITE_APP_ENV: 'local',
      VITE_JOB_POLL_INTERVAL_MS: '100',
    },
  },
});
