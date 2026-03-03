// spec: tests/web/security-test-plan.md
// seed: tests/web/seed.spec.ts

import { test, expect } from '@playwright/test';

test.describe('Authentication Flow', () => {
  test('should login successfully with valid admin API key', async ({ page }) => {
    // 1. Navigate to http://localhost:3007/login
    await page.goto('http://localhost:3007/login');

    // 2. Enter valid admin API key: "dk_PE2-k0faXI3JBhW-tYWqPPzbJxpqlWHsXG_SMNZU8bo"
    await page.getByRole('textbox', { name: 'API Key' }).fill('dk_PE2-k0faXI3JBhW-tYWqPPzbJxpqlWHsXG_SMNZU8bo');

    // 3. Click the Login button
    await page.getByRole('button', { name: 'Login' }).click();

    // 4. Verify redirect to dashboard (URL should not contain /login)
    await expect(page).not.toHaveURL(/\/login/);

    // 5. Verify dashboard content is visible (sidebar with navigation items)
    await expect(page.getByRole('link', { name: 'Dashboard' })).toBeVisible();
    await expect(page.getByRole('link', { name: 'Batch Jobs' })).toBeVisible();
  });
});
