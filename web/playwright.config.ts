import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './e2e',
  timeout: 30000,
  use: {
    baseURL: 'http://127.0.0.1:4173',
    viewport: { width: 375, height: 667 },
    channel: 'chrome',
    headless: true,
  },
  webServer: {
    command: 'npx vite --host 127.0.0.1 --port 4173',
    url: 'http://127.0.0.1:4173',
    reuseExistingServer: !process.env.CI,
    env: {
      VITE_USE_MOCK_API: 'true',
      VITE_APP_ENV: 'local',
      VITE_JOB_POLL_INTERVAL_MS: '100',
    },
  },
});
