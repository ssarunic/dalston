import { test, expect } from '@playwright/test'

// Mock namespace list response
const NAMESPACES_RESPONSE = {
  namespaces: [
    { namespace: 'rate_limits', label: 'Rate Limits', description: 'Control API request and concurrency limits', editable: true, setting_count: 3, has_overrides: false },
    { namespace: 'engines', label: 'Engines', description: 'Engine availability and timeout behavior', editable: true, setting_count: 2, has_overrides: false },
    { namespace: 'audio', label: 'Audio', description: 'Audio download size and timeout constraints', editable: true, setting_count: 2, has_overrides: false },
    { namespace: 'retention', label: 'Retention', description: 'Data retention cleanup intervals and limits', editable: true, setting_count: 3, has_overrides: false },
    { namespace: 'system', label: 'System', description: 'Infrastructure configuration (read-only)', editable: false, setting_count: 0, has_overrides: false },
  ],
}

// Mock rate_limits namespace response
const RATE_LIMITS_RESPONSE = {
  namespace: 'rate_limits',
  label: 'Rate Limits',
  description: 'Control API request and concurrency limits',
  editable: true,
  settings: [
    { key: 'requests_per_minute', label: 'Requests per minute', description: 'Maximum API requests per minute per tenant', value_type: 'int', value: 600, default_value: 600, is_overridden: false, env_var: 'RATE_LIMIT_REQUESTS_PER_MINUTE', min_value: 1, max_value: 100000 },
    { key: 'concurrent_jobs', label: 'Max concurrent batch jobs', description: 'Maximum concurrent batch transcription jobs per tenant', value_type: 'int', value: 10, default_value: 10, is_overridden: false, env_var: 'RATE_LIMIT_CONCURRENT_JOBS', min_value: 1, max_value: 1000 },
    { key: 'concurrent_sessions', label: 'Max concurrent realtime sessions', description: 'Maximum concurrent realtime WebSocket sessions per tenant', value_type: 'int', value: 5, default_value: 5, is_overridden: false, env_var: 'RATE_LIMIT_CONCURRENT_SESSIONS', min_value: 1, max_value: 1000 },
  ],
  updated_at: null,
}

// Mock engines namespace with select type
const ENGINES_RESPONSE = {
  namespace: 'engines',
  label: 'Engines',
  description: 'Engine availability and timeout behavior',
  editable: true,
  settings: [
    { key: 'unavailable_behavior', label: 'Unavailable engine behavior', description: 'Action when a required engine is not running', value_type: 'select', value: 'fail_fast', default_value: 'fail_fast', is_overridden: false, env_var: 'ENGINE_UNAVAILABLE_BEHAVIOR', options: ['fail_fast', 'wait'] },
    { key: 'wait_timeout_seconds', label: 'Engine wait timeout (seconds)', description: 'How long to wait for an engine before failing', value_type: 'int', value: 300, default_value: 300, is_overridden: false, env_var: 'ENGINE_WAIT_TIMEOUT_SECONDS', min_value: 10, max_value: 3600 },
  ],
  updated_at: null,
}

// Mock system namespace response
const SYSTEM_RESPONSE = {
  namespace: 'system',
  label: 'System',
  description: 'Infrastructure configuration (read-only)',
  editable: false,
  settings: [
    { key: 'redis_url', label: 'Redis URL', description: '', value_type: 'string', value: 'redis://redis:6379', default_value: 'redis://redis:6379', is_overridden: false, env_var: '' },
    { key: 'database_url', label: 'Database', description: '', value_type: 'string', value: 'postgresql+asyncpg://dalston:****@db:5432/dalston', default_value: 'postgresql+asyncpg://dalston:****@db:5432/dalston', is_overridden: false, env_var: '' },
    { key: 's3_bucket', label: 'S3 Bucket', description: '', value_type: 'string', value: 'dalston-artifacts', default_value: 'dalston-artifacts', is_overridden: false, env_var: '' },
    { key: 's3_region', label: 'S3 Region', description: '', value_type: 'string', value: 'eu-west-2', default_value: 'eu-west-2', is_overridden: false, env_var: '' },
    { key: 'version', label: 'Version', description: '', value_type: 'string', value: '0.1.0', default_value: '0.1.0', is_overridden: false, env_var: '' },
  ],
  updated_at: null,
}

function setupRoutes(page: import('@playwright/test').Page) {
  return page.route('**/api/console/settings**', (route, request) => {
    const url = new URL(request.url())
    const path = url.pathname

    if (path === '/api/console/settings') {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(NAMESPACES_RESPONSE),
      })
    }

    if (path === '/api/console/settings/rate_limits') {
      if (request.method() === 'PATCH') {
        const updated = { ...RATE_LIMITS_RESPONSE, updated_at: new Date().toISOString() }
        updated.settings = updated.settings.map((s) => ({ ...s }))
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify(updated),
        })
      }
      if (request.method() === 'POST') {
        // Reset endpoint
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify(RATE_LIMITS_RESPONSE),
        })
      }
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(RATE_LIMITS_RESPONSE),
      })
    }

    if (path === '/api/console/settings/engines') {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(ENGINES_RESPONSE),
      })
    }

    if (path === '/api/console/settings/system') {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(SYSTEM_RESPONSE),
      })
    }

    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(RATE_LIMITS_RESPONSE),
    })
  })
}

test.describe('Settings Page', () => {
  test.beforeEach(async ({ page }) => {
    await page.addInitScript(() => {
      sessionStorage.setItem('dalston_api_key', 'test-admin-key')
    })
    // Mock auth/me endpoint for protected route
    await page.route('**/auth/me', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ id: 'test', scopes: ['admin'], prefix: 'dk_test1234' }),
      })
    )
  })

  test('renders settings page with all namespace tabs', async ({ page }) => {
    await setupRoutes(page)
    await page.goto('/console/settings')

    await expect(page.getByRole('heading', { name: 'Settings' })).toBeVisible()
    await expect(page.getByText('Rate Limits')).toBeVisible()
    await expect(page.getByText('Engines')).toBeVisible()
    await expect(page.getByText('Audio')).toBeVisible()
    await expect(page.getByText('Retention')).toBeVisible()
    await expect(page.getByText('System')).toBeVisible()
  })

  test('shows rate limit settings with default values', async ({ page }) => {
    await setupRoutes(page)
    await page.goto('/console/settings')

    await expect(page.getByText('Requests per minute')).toBeVisible()
    await expect(page.getByText('Max concurrent batch jobs')).toBeVisible()
    await expect(page.getByText('Max concurrent realtime sessions')).toBeVisible()
  })

  test('tab switching syncs with URL', async ({ page }) => {
    await setupRoutes(page)
    await page.goto('/console/settings')

    // Click Engines tab
    await page.getByRole('button', { name: /Engines/ }).click()
    await expect(page).toHaveURL(/tab=engines/)
    await expect(page.getByText('Unavailable engine behavior')).toBeVisible()

    // Click System tab
    await page.getByRole('button', { name: /System/ }).click()
    await expect(page).toHaveURL(/tab=system/)
    await expect(page.getByText('System Information')).toBeVisible()
  })

  test('system tab shows read-only info with copy buttons', async ({ page }) => {
    await setupRoutes(page)
    await page.goto('/console/settings?tab=system')

    await expect(page.getByText('System Information')).toBeVisible()
    await expect(page.getByText('read-only')).toBeVisible()
    await expect(page.getByText('redis://redis:6379')).toBeVisible()
    await expect(page.getByText('dalston-artifacts')).toBeVisible()
    await expect(page.getByText('0.1.0')).toBeVisible()
  })

  test('editing a value shows unsaved changes bar', async ({ page }) => {
    await setupRoutes(page)
    await page.goto('/console/settings')

    // Get the requests_per_minute input and change it
    const input = page.locator('input[type="number"]').first()
    await input.fill('1200')

    // Save bar should appear
    await expect(page.getByText('unsaved')).toBeVisible()
    await expect(page.getByRole('button', { name: 'Cancel' })).toBeVisible()
    await expect(page.getByRole('button', { name: 'Save' })).toBeVisible()
  })

  test('cancel reverts changes', async ({ page }) => {
    await setupRoutes(page)
    await page.goto('/console/settings')

    const input = page.locator('input[type="number"]').first()
    await input.fill('1200')

    // Verify save bar appeared
    await expect(page.getByText('unsaved')).toBeVisible()

    // Click cancel
    await page.getByRole('button', { name: 'Cancel' }).click()

    // Save bar should disappear
    await expect(page.getByText('unsaved')).not.toBeVisible()
  })

  test('save sends PATCH and shows success', async ({ page }) => {
    await setupRoutes(page)
    await page.goto('/console/settings')

    const input = page.locator('input[type="number"]').first()
    await input.fill('1200')

    await page.getByRole('button', { name: 'Save' }).click()

    await expect(page.getByText('saved successfully')).toBeVisible()
  })

  test('sidebar has settings link', async ({ page }) => {
    await setupRoutes(page)
    // Also mock other required endpoints
    await page.route('**/api/console/dashboard', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          system: { healthy: true, version: '0.1.0' },
          batch: { running_jobs: 0, queued_jobs: 0, completed_today: 0, failed_today: 0 },
          realtime: { total_capacity: 0, used_capacity: 0, available_capacity: 0, worker_count: 0, ready_workers: 0 },
          recent_jobs: [],
        }),
      })
    )
    await page.goto('/console/')

    const settingsLink = page.getByRole('link', { name: 'Settings' })
    await expect(settingsLink).toBeVisible()
  })

  test('select dropdown works on engines tab', async ({ page }) => {
    await setupRoutes(page)
    await page.goto('/console/settings?tab=engines')

    await expect(page.getByText('Unavailable engine behavior')).toBeVisible()

    // Change the select value
    const select = page.locator('select')
    await select.selectOption('wait')
    await expect(page.getByText('unsaved')).toBeVisible()
  })

  test('conflict error (409) shows message', async ({ page }) => {
    // Override the PATCH to return 409
    await page.route('**/api/console/settings/**', (route, request) => {
      const url = new URL(request.url())
      if (request.method() === 'PATCH') {
        return route.fulfill({
          status: 409,
          contentType: 'application/json',
          body: JSON.stringify({ detail: 'Settings were modified by another admin. Please refresh and try again.' }),
        })
      }
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(RATE_LIMITS_RESPONSE),
      })
    })
    await page.route('**/api/console/settings', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(NAMESPACES_RESPONSE),
      })
    )
    await page.goto('/console/settings')

    const input = page.locator('input[type="number"]').first()
    await input.fill('999')

    await page.getByRole('button', { name: 'Save' }).click()

    await expect(page.getByText('modified by another admin')).toBeVisible()
  })
})
