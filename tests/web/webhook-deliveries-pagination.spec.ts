import { test, expect } from '@playwright/test'

const ENDPOINT_ID = 'webhook-endpoint-123'

function makeDelivery(
  index: number,
  status: 'failed' | 'success' = 'success',
  eventType = 'transcription.completed'
) {
  return {
    id: `delivery-${String(index).padStart(8, '0')}`,
    endpoint_id: ENDPOINT_ID,
    job_id: `job-${String(index).padStart(8, '0')}`,
    event_type: eventType,
    status,
    attempts: status === 'failed' ? 3 : 1,
    last_attempt_at: new Date(Date.now() - index * 30_000).toISOString(),
    last_status_code: status === 'failed' ? 500 : 200,
    last_error: status === 'failed' ? 'mock delivery failure' : null,
    created_at: new Date(Date.now() - index * 60_000).toISOString(),
  }
}

function makeDeliveriesPage(startIndex: number, count: number, hasMore: boolean, cursor: string | null) {
  return {
    deliveries: Array.from({ length: count }, (_, i) => makeDelivery(startIndex + i)),
    cursor,
    has_more: hasMore,
  }
}

test.describe('Webhook Deliveries', () => {
  test('Sort change reloads server-ordered deliveries', async ({ page }) => {
    await page.addInitScript(() => {
      sessionStorage.setItem('dalston_api_key', 'test-admin-key')
    })

    await page.route('**/v1/webhooks/**/deliveries*', (route, request) => {
      const url = new URL(request.url())
      const sort = url.searchParams.get('sort')

      if (sort === 'created_asc') {
        route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            deliveries: [
              makeDelivery(9001, 'success', 'delivery.asc.1'),
              makeDelivery(9002, 'success', 'delivery.asc.2'),
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
          deliveries: [
            makeDelivery(1, 'success', 'delivery.desc.1'),
            makeDelivery(2, 'success', 'delivery.desc.2'),
          ],
          cursor: null,
          has_more: false,
        }),
      })
    })

    await page.route(/\/v1\/webhooks(\?.*)?$/, (route) => {
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          endpoints: [
            {
              id: ENDPOINT_ID,
              url: 'https://example.com/webhook',
              events: ['transcription.completed', 'transcription.failed'],
              description: 'Mock endpoint',
              is_active: true,
              disabled_reason: null,
              consecutive_failures: 0,
              last_success_at: null,
              created_at: new Date().toISOString(),
              updated_at: new Date().toISOString(),
            },
          ],
        }),
      })
    })

    await page.goto(`/console/webhooks/${ENDPOINT_ID}`)

    const firstEventCell = page.locator('tbody tr').first().locator('td').first()
    await expect(firstEventCell).toContainText('delivery.desc.1')

    const sortControl = page
      .locator('select')
      .filter({ has: page.locator('option[value="created_asc"]') })

    await sortControl.selectOption('created_asc')

    await expect(page).toHaveURL(
      new RegExp(`/console/webhooks/${ENDPOINT_ID}\\?(?=.*sort=created_asc)`)
    )
    await expect(firstEventCell).toContainText('delivery.asc.1')
  })

  test('Load More increases rows and status/sort/limit update URL', async ({ page }) => {
    await page.addInitScript(() => {
      sessionStorage.setItem('dalston_api_key', 'test-admin-key')
    })

    await page.route('**/v1/webhooks/**/deliveries*', (route, request) => {
      const url = new URL(request.url())
      const cursor = url.searchParams.get('cursor')
      const status = url.searchParams.get('status')
      const sort = url.searchParams.get('sort')

      if (status === 'failed') {
        route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            deliveries: [makeDelivery(201, 'failed'), makeDelivery(202, 'failed'), makeDelivery(203, 'failed')],
            cursor: null,
            has_more: false,
          }),
        })
        return
      }

      if (sort === 'created_asc') {
        route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify(makeDeliveriesPage(301, 20, false, null)),
        })
        return
      }

      if (!cursor) {
        route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify(makeDeliveriesPage(1, 20, true, 'cursor-page-2')),
        })
        return
      }

      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(makeDeliveriesPage(21, 20, false, null)),
      })
    })

    await page.route(/\/v1\/webhooks(\?.*)?$/, (route) => {
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          endpoints: [
            {
              id: ENDPOINT_ID,
              url: 'https://example.com/webhook',
              events: ['transcription.completed', 'transcription.failed'],
              description: 'Mock endpoint',
              is_active: true,
              disabled_reason: null,
              consecutive_failures: 0,
              last_success_at: null,
              created_at: new Date().toISOString(),
              updated_at: new Date().toISOString(),
            },
          ],
        }),
      })
    })

    await page.goto(`/console/webhooks/${ENDPOINT_ID}`)

    await expect(page.getByText('Showing 20 deliveries')).toBeVisible()
    await page.getByRole('button', { name: 'Load More' }).click()
    await expect(page.getByText('Showing 40 deliveries')).toBeVisible()

    const statusControl = page
      .locator('select')
      .filter({ has: page.locator('option[value="failed"]') })
    const sortControl = page
      .locator('select')
      .filter({ has: page.locator('option[value="created_asc"]') })
    const limitControl = page
      .locator('select')
      .filter({ has: page.locator('option[value="50"]') })

    await sortControl.selectOption('created_asc')
    await limitControl.selectOption('50')
    await statusControl.selectOption('failed')
    await expect(page).toHaveURL(
      new RegExp(`/console/webhooks/${ENDPOINT_ID}\\?(?=.*status=failed)(?=.*sort=created_asc)(?=.*limit=50)`)
    )
    await expect(page.getByText('Showing 3 deliveries')).toBeVisible()
  })
})
