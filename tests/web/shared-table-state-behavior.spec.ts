import { test, expect } from '@playwright/test'

function makeJob(index: number) {
  return {
    id: `job-${String(index).padStart(8, '0')}-0000-0000-0000-000000000000`,
    status: 'completed',
    created_at: new Date(Date.now() - index * 60_000).toISOString(),
    audio_duration_seconds: 120,
    result_language_code: 'en',
    result_word_count: 500,
  }
}

function makeAuditEvent(index: number) {
  return {
    id: index,
    timestamp: new Date(Date.now() - index * 60_000).toISOString(),
    correlation_id: null,
    tenant_id: 'default',
    actor_type: 'api_key',
    actor_id: `dk_test_${String(index).padStart(8, '0')}`,
    action: 'job.created',
    resource_type: 'job',
    resource_id: `resource-${String(index).padStart(8, '0')}`,
    detail: null,
    ip_address: '127.0.0.1',
    user_agent: 'playwright',
  }
}

test.describe('Shared table state behavior', () => {
  test('Invalid URL params fall back to default request filters on Batch Jobs', async ({ page }) => {
    await page.addInitScript(() => {
      sessionStorage.setItem('dalston_api_key', 'test-admin-key')
    })

    const seenRequests: URL[] = []
    await page.route('**/api/console/jobs*', (route, request) => {
      const url = new URL(request.url())
      seenRequests.push(url)
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          jobs: [makeJob(1)],
          cursor: null,
          has_more: false,
        }),
      })
    })

    await page.goto('/console/jobs?status=invalid&sort=wrong&limit=999')
    await expect(page.getByText('Showing 1 jobs')).toBeVisible()

    expect(seenRequests.length).toBeGreaterThan(0)
    const first = seenRequests[0]
    expect(first.searchParams.get('status')).toBeNull()
    expect(first.searchParams.get('sort')).toBe('created_desc')
    expect(first.searchParams.get('limit')).toBe('20')
  })

  test('Clear filters resets shared and domain-specific params on Audit Log', async ({ page }) => {
    await page.addInitScript(() => {
      sessionStorage.setItem('dalston_api_key', 'test-admin-key')
    })

    await page.route('**/v1/audit*', (route) => {
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          events: [makeAuditEvent(1)],
          cursor: null,
          has_more: false,
        }),
      })
    })

    await page.goto('/console/audit?resource_type=job&action=job.created&actor_id=abc&sort=timestamp_asc&limit=100')
    await expect(page.getByText('Showing 1 events')).toBeVisible()

    await page.getByRole('button', { name: 'Filters' }).click()
    await page.getByRole('button', { name: 'Clear' }).click()

    await expect(page).toHaveURL('/console/audit')
  })
})
