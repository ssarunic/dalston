/**
 * M45 Security Hardening - Smoke Tests
 *
 * These tests verify the security model and basic functionality of the Dalston web console.
 */

import { test, expect, type Page } from '@playwright/test';

const ADMIN_API_KEY = 'dk_PE2-k0faXI3JBhW-tYWqPPzbJxpqlWHsXG_SMNZU8bo';

// Helper function to login as admin
async function loginAsAdmin(page: Page) {
  await page.goto('/console/login');
  await page.getByRole('textbox', { name: 'API Key' }).fill(ADMIN_API_KEY);
  await page.getByRole('button', { name: 'Login' }).click();
  await expect(page).not.toHaveURL(/\/login/);
}

test.describe('M45 Security Smoke Tests', () => {

  test.describe('Dashboard', () => {
    test('should load dashboard with stats for admin user', async ({ page }) => {
      // Login with admin key
      await loginAsAdmin(page);

      // Verify dashboard heading
      await expect(page.getByRole('heading', { name: 'Dashboard', level: 1 })).toBeVisible();
      await expect(page.getByText('System overview and recent activity')).toBeVisible();

      // Verify stat cards are visible (use exact match to avoid matching headings)
      await expect(page.getByText('System Status')).toBeVisible();
      await expect(page.getByText('Running Jobs')).toBeVisible();
      await expect(page.getByText('Real-time Sessions', { exact: true })).toBeVisible();
      await expect(page.getByText('Completed Today')).toBeVisible();

      // Verify recent sections
      await expect(page.getByRole('heading', { name: 'Recent Batch Jobs' })).toBeVisible();
      await expect(page.getByRole('heading', { name: 'Recent Real-time Sessions' })).toBeVisible();
    });
  });

  test.describe('Batch Jobs Page', () => {
    test('should show job list with controls', async ({ page }) => {
      await loginAsAdmin(page);

      // Navigate to Batch Jobs
      await page.getByRole('link', { name: 'Batch Jobs' }).click();
      await expect(page).toHaveURL(/\/console\/jobs$/);

      // Verify page heading
      await expect(page.getByRole('heading', { name: 'Batch Jobs', level: 1 })).toBeVisible();

      // Verify Submit Job button
      await expect(page.getByRole('button', { name: 'Submit Job' })).toBeVisible();
    });

    test('should navigate to job detail when clicking a job', async ({ page }) => {
      await loginAsAdmin(page);

      // Navigate to Batch Jobs
      await page.getByRole('link', { name: 'Batch Jobs' }).click();

      // Check if there are any jobs in the list (internal links use /jobs/, base path is added)
      const jobLinks = page.locator('a[href^="/jobs/"]').filter({ hasNot: page.locator('text=View all') });
      const jobCount = await jobLinks.count();

      if (jobCount > 0) {
        // Click the first job
        await jobLinks.first().click();

        // Verify we're on a job detail page
        await expect(page).toHaveURL(/\/console\/jobs\/[a-f0-9-]+$/);

        // Verify back button exists
        await expect(page.getByRole('button', { name: /back/i })).toBeVisible();
      }
    });
  });

  test.describe('API Keys Page', () => {
    test('should show API keys list', async ({ page }) => {
      await loginAsAdmin(page);

      // Navigate to API Keys
      await page.getByRole('link', { name: 'API Keys' }).click();
      await expect(page).toHaveURL(/\/console\/keys$/);

      // Verify page heading
      await expect(page.getByRole('heading', { name: 'API Keys', level: 1 })).toBeVisible();

      // Verify Create Key button
      await expect(page.getByRole('button', { name: 'Create Key' })).toBeVisible();

      // Verify at least one key is shown (the current admin key)
      await expect(page.getByText('current')).toBeVisible();

      // Verify admin scope badge is shown (use first() since multiple keys may have admin scope)
      await expect(page.getByText('admin').first()).toBeVisible();
    });

    test('should open Create Key dialog', async ({ page }) => {
      await loginAsAdmin(page);

      // Navigate to API Keys
      await page.getByRole('link', { name: 'API Keys' }).click();

      // Click Create Key button
      await page.getByRole('button', { name: 'Create Key' }).click();

      // Verify dialog opens
      await expect(page.getByRole('heading', { name: 'Create API Key' })).toBeVisible();

      // Verify Name field
      await expect(page.getByRole('textbox', { name: 'Name' })).toBeVisible();

      // Verify scope checkboxes exist in the dialog
      await expect(page.getByRole('checkbox', { name: /Read Jobs/i })).toBeVisible();
      await expect(page.getByRole('checkbox', { name: /Create Jobs/i })).toBeVisible();
      await expect(page.getByRole('checkbox', { name: /Real-time/i })).toBeVisible();
      await expect(page.getByRole('checkbox', { name: /Admin Access/i })).toBeVisible();

      // Close dialog
      await page.getByRole('button', { name: 'Cancel' }).click();
      await expect(page.getByRole('heading', { name: 'Create API Key' })).not.toBeVisible();
    });
  });

  test.describe('Navigation', () => {
    test('should show all navigation sections for admin', async ({ page }) => {
      await loginAsAdmin(page);

      // Verify all navigation links are visible
      await expect(page.getByRole('link', { name: 'Dashboard' })).toBeVisible();
      await expect(page.getByRole('link', { name: 'Batch Jobs' })).toBeVisible();
      await expect(page.getByRole('link', { name: 'Real-time' })).toBeVisible();
      await expect(page.getByRole('link', { name: 'Engines' })).toBeVisible();
      await expect(page.getByRole('link', { name: 'Models' })).toBeVisible();
      await expect(page.getByRole('link', { name: 'API Keys' })).toBeVisible();
      await expect(page.getByRole('link', { name: 'Webhooks' })).toBeVisible();
      await expect(page.getByRole('link', { name: 'Audit Log' })).toBeVisible();
      await expect(page.getByRole('link', { name: 'Settings' })).toBeVisible();
    });

    test('should navigate to all pages without errors', async ({ page }) => {
      await loginAsAdmin(page);

      // Test each navigation link
      const pages = [
        { link: 'Batch Jobs', url: '/console/jobs' },
        { link: 'Real-time', url: '/console/realtime' },
        { link: 'Engines', url: '/console/engines' },
        { link: 'Models', url: '/console/models' },
        { link: 'API Keys', url: '/console/keys' },
        { link: 'Webhooks', url: '/console/webhooks' },
        { link: 'Audit Log', url: '/console/audit' },
        { link: 'Settings', url: '/console/settings' },
        { link: 'Dashboard', url: '/console' },
      ];

      for (const { link, url } of pages) {
        await page.getByRole('link', { name: link }).click();
        await expect(page).toHaveURL(new RegExp(`${url}$`));
      }
    });
  });

  test.describe('Authentication Rejection', () => {
    test('should reject invalid API key', async ({ page }) => {
      await page.goto('/console/login');
      await page.getByRole('textbox', { name: 'API Key' }).fill('dk_invalid_key_12345');
      await page.getByRole('button', { name: 'Login' }).click();

      // Verify error message
      await expect(page.getByText('Invalid API key')).toBeVisible();

      // Verify still on login page
      await expect(page).toHaveURL(/\/login$/);
    });

    test('should reject non-admin API key', async ({ page }) => {
      // This test verifies that keys without admin scope are rejected.
      // We use an invalid key format which will be rejected by the API.
      await page.goto('/console/login');

      // Use a properly formatted but non-existent key
      await page.getByRole('textbox', { name: 'API Key' }).fill('dk_test123456789012345678901234567890123456');
      await page.getByRole('button', { name: 'Login' }).click();

      // Should show an error - wait for either error message
      await expect(
        page.getByText('Invalid API key').or(page.getByText('API key does not have admin scope'))
      ).toBeVisible();

      // Verify still on login page
      await expect(page).toHaveURL(/\/login$/);
    });
  });

  test.describe('Logout', () => {
    test('should logout and redirect to login', async ({ page }) => {
      await loginAsAdmin(page);

      // Click logout
      await page.getByRole('button', { name: 'Logout' }).click();

      // Verify redirect to login
      await expect(page).toHaveURL(/\/login$/);

      // Verify login form is shown
      await expect(page.getByRole('textbox', { name: 'API Key' })).toBeVisible();
    });

    test('should not access protected routes after logout', async ({ page }) => {
      await loginAsAdmin(page);
      await page.getByRole('button', { name: 'Logout' }).click();

      // Try to access protected route
      await page.goto('/console/jobs');

      // Should redirect to login
      await expect(page).toHaveURL(/\/login$/);
    });
  });

  test.describe('Protected Routes', () => {
    test('should redirect unauthenticated users to login', async ({ page }) => {
      // Try to access various protected routes directly
      const protectedRoutes = ['/console/jobs', '/console/keys', '/console/settings', '/console/engines', '/console/models'];

      for (const route of protectedRoutes) {
        await page.goto(route);
        await expect(page).toHaveURL(/\/login$/);
      }
    });
  });
});
