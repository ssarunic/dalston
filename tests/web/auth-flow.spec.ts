// spec: tests/web/security-test-plan.md
// seed: tests/web/seed.spec.ts

import { test, expect } from '@playwright/test';

const ADMIN_API_KEY = 'dk_PE2-k0faXI3JBhW-tYWqPPzbJxpqlWHsXG_SMNZU8bo';

test.describe('Authentication Flow', () => {
  test('should login successfully with valid admin API key', async ({ page }) => {
    // Navigate to login page (uses baseURL from playwright.config.ts)
    await page.goto('/console/login');

    // Enter valid admin API key
    await page.getByRole('textbox', { name: 'API Key' }).fill(ADMIN_API_KEY);

    // Click the Login button
    await page.getByRole('button', { name: 'Login' }).click();

    // Verify redirect to dashboard (URL should not contain /login)
    await expect(page).not.toHaveURL(/\/login/);

    // Verify dashboard content is visible (sidebar with navigation items)
    await expect(page.getByRole('link', { name: 'Dashboard' })).toBeVisible();
    await expect(page.getByRole('link', { name: 'Batch Jobs' })).toBeVisible();
  });
});
