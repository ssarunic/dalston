import { test, expect } from '@playwright/test'

function makeAuditEvent(
  index: number,
  action = 'audit.default',
  resourceType = 'job'
) {
  return {
    id: index,
    timestamp: new Date(Date.now() - index * 60_000).toISOString(),
    correlation_id: null,
    tenant_id: 'default',
    actor_type: 'api_key',
    actor_id: `dk_test_${String(index).padStart(8, '0')}`,
    action,
    resource_type: resourceType,
    resource_id: `resource-${String(index).padStart(8, '0')}`,
    detail: null,
    ip_address: '127.0.0.1',
    user_agent: 'playwright',
  }
}

function makeAuditPage(
  startIndex: number,
  count: number,
  hasMore: boolean,
  cursor: string | null
) {
  return {
    events: Array.from({ length: count }, (_, i) => makeAuditEvent(startIndex + i)),
    cursor,
    has_more: hasMore,
  }
}

test.describe('Audit Log', () => {
  test('Sort change reloads server-ordered events', async ({ page }) => {
    await page.addInitScript(() => {
      sessionStorage.setItem('dalston_api_key', 'test-admin-key')
    })

    await page.route('**/v1/audit*', (route, request) => {
      const url = new URL(request.url())
      const sort = url.searchParams.get('sort')

      if (sort === 'timestamp_asc') {
        route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            events: [
              makeAuditEvent(9001, 'audit.asc.1'),
              makeAuditEvent(9002, 'audit.asc.2'),
            ],
            cursor: null,
            has_more: false,
          }),
        })
        return
      }

      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          events: [
            makeAuditEvent(1, 'audit.desc.1'),
            makeAuditEvent(2, 'audit.desc.2'),
          ],
          cursor: null,
          has_more: false,
        }),
      })
    })

    await page.goto('/console/audit')

    const firstActionCell = page.locator('tbody tr').first().locator('td').nth(2)
    await expect(firstActionCell).toContainText('audit.desc.1')

    await page.getByRole('button', { name: 'Filters' }).click()
    await page.getByRole('button', { name: 'timestamp_desc' }).click()
    await page.getByRole('option', { name: 'Oldest first' }).click()

    await expect(page).toHaveURL(/\/console\/audit\?(?=.*sort=timestamp_asc)/)
    await expect(firstActionCell).toContainText('audit.asc.1')
  })

  test('Load More increases rows and resource/sort/limit update URL', async ({ page }) => {
    await page.addInitScript(() => {
      sessionStorage.setItem('dalston_api_key', 'test-admin-key')
    })

    await page.route('**/v1/audit*', (route, request) => {
      const url = new URL(request.url())
      const cursor = url.searchParams.get('cursor')
      const resourceType = url.searchParams.get('resource_type')
      const sort = url.searchParams.get('sort')

      if (resourceType === 'job') {
        route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            events: [
              makeAuditEvent(201, 'audit.job.1', 'job'),
              makeAuditEvent(202, 'audit.job.2', 'job'),
              makeAuditEvent(203, 'audit.job.3', 'job'),
            ],
            cursor: null,
            has_more: false,
          }),
        })
        return
      }

      if (sort === 'timestamp_asc') {
        route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify(makeAuditPage(301, 20, false, null)),
        })
        return
      }

      if (!cursor) {
        route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify(makeAuditPage(1, 20, true, 'cursor-page-2')),
        })
        return
      }

      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(makeAuditPage(21, 20, false, null)),
      })
    })

    await page.goto('/console/audit')

    await expect(page.getByText('Showing 20 events')).toBeVisible()
    await page.getByRole('button', { name: 'Load More' }).click()
    await expect(page.getByText('Showing 40 events')).toBeVisible()

    await page.getByRole('button', { name: 'Filters' }).click()
    await page.getByRole('button', { name: 'All Resources' }).click()
    await page.getByRole('option', { name: 'Job' }).click()

    await page.getByRole('button', { name: 'timestamp_desc' }).click()
    await page.getByRole('option', { name: 'Oldest first' }).click()

    await page.getByRole('button', { name: '50' }).click()
    await page.getByRole('option', { name: '100' }).click()

    await expect(page).toHaveURL(
      /\/console\/audit\?(?=.*resource_type=job)(?=.*sort=timestamp_asc)(?=.*limit=100)/
    )
    await expect(page.getByText('Showing 3 events')).toBeVisible()
  })
})
