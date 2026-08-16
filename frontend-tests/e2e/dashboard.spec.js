import { expect, test } from '@playwright/test';

test.skip(!process.env.SIFTARR_E2E_API_KEY, 'Set SIFTARR_E2E_API_KEY to the server API key');

test('an API-key-authenticated browser can render the dashboard', async ({ page }) => {
  const pageErrors = [];
  page.on('pageerror', (error) => pageErrors.push(error.message));

  const response = await page.goto('/dashboard');

  expect(response?.status()).toBe(200);
  await expect(page).toHaveTitle('Dashboard - Siftarr');
  await expect(page.getByRole('heading', { name: 'Dashboard', level: 1 })).toBeVisible();
  await expect.poll(() => page.evaluate(() => typeof window.filterTable)).toBe('function');
  expect(pageErrors).toEqual([]);
});
