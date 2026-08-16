import { defineConfig, devices } from '@playwright/test';

const apiKey = process.env.SIFTARR_E2E_API_KEY;

export default defineConfig({
  testDir: './frontend-tests/e2e',
  outputDir: '/tmp/opencode/siftarr-playwright-results',
  reporter: 'line',
  use: {
    baseURL: process.env.SIFTARR_E2E_BASE_URL || 'http://127.0.0.1:8000',
    extraHTTPHeaders: apiKey ? { 'X-API-Key': apiKey } : {},
    trace: 'retain-on-failure',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
});
