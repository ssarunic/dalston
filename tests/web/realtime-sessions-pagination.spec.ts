import { test, expect } from '@playwright/test'

function makeSession(
  index: number,
  status: 'active' | 'completed' | 'error' | 'interrupted' = 'completed',
  model = 'model.default'
) {
  return {
    id: `session-${String(index).padStart(8, '0')}-0000-0000-0000-000000000000`,
    status,
    language: 'en',
    model,
    engine: 'rt-engine',
    audio_duration_seconds: 120,
    segment_count: 24,
    word_count: 500,
    store_audio: true,
    store_transcript: true,
    started_at: new Date(Date.now() - index * 60_000).toISOString(),
    ended_at: status === 'active' ? null : new Date(Date.now() - index * 30_000).toISOString(),
  }
}

function makeSessionsPage(
  startIndex: number,
  count: number,
  hasMore: boolean,
  cursor: string | null
) {
  return {
    sessions: Array.from({ length: count }, (_, i) => makeSession(startIndex + i)),
    cursor,
    has_more: hasMore,
  }
}

test.describe('Realtime Sessions', () => {
  test('Sort change reloads server-ordered sessions', async ({ page }) => {
    await page.addInitScript(() => {
      sessionStorage.setItem('dalston_api_key', 'test-admin-key')
    })

    await page.route('**/v1/realtime/status*', (route) => {
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          status: 'ready',
          total_capacity: 8,
          active_sessions: 1,
          available_capacity: 7,
          worker_count: 2,
          ready_workers: 2,
        }),
      })
    })

    await page.route('**/v1/realtime/sessions*', (route, request) => {
      const url = new URL(request.url())
      const sort = url.searchParams.get('sort')

      if (sort === 'started_asc') {
        route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            sessions: [
              makeSession(9001, 'completed', 'session.asc.1'),
              makeSession(9002, 'completed', 'session.asc.2'),
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
          sessions: [
            makeSession(1, 'completed', 'session.desc.1'),
            makeSession(2, 'completed', 'session.desc.2'),
          ],
          cursor: null,
          has_more: false,
        }),
      })
    })

    await page.goto('/console/realtime')

    const firstModelCell = page.locator('tbody tr').first().locator('td').nth(2)
    await expect(firstModelCell).toContainText('session.desc.1')

    await page.getByRole('button', { name: 'Filters' }).click()
    await page.getByRole('button', { name: 'started_desc' }).click()
    await page.getByRole('option', { name: 'Oldest first' }).click()

    await expect(page).toHaveURL(/\/console\/realtime\?(?=.*sort=started_asc)/)
    await expect(firstModelCell).toContainText('session.asc.1')
  })

  test('Load More increases rows and status/sort/limit update URL', async ({ page }) => {
    await page.addInitScript(() => {
      sessionStorage.setItem('dalston_api_key', 'test-admin-key')
    })

    await page.route('**/v1/realtime/status*', (route) => {
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          status: 'ready',
          total_capacity: 8,
          active_sessions: 1,
          available_capacity: 7,
          worker_count: 2,
          ready_workers: 2,
        }),
      })
    })

    await page.route('**/v1/realtime/sessions*', (route, request) => {
      const url = new URL(request.url())
      const cursor = url.searchParams.get('cursor')
      const status = url.searchParams.get('status')
      const sort = url.searchParams.get('sort')

      if (status === 'error') {
        route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            sessions: [makeSession(201, 'error'), makeSession(202, 'error')],
            cursor: null,
            has_more: false,
          }),
        })
        return
      }

      if (sort === 'started_asc') {
        route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify(makeSessionsPage(301, 20, false, null)),
        })
        return
      }

      if (!cursor) {
        route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify(makeSessionsPage(1, 20, true, 'cursor-page-2')),
        })
        return
      }

      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(makeSessionsPage(21, 20, false, null)),
      })
    })

    await page.goto('/console/realtime')

    await expect(page.getByText('Showing 20 sessions')).toBeVisible()
    await page.getByRole('button', { name: 'Load More' }).click()
    await expect(page.getByText('Showing 40 sessions')).toBeVisible()

    await page.getByRole('button', { name: 'Filters' }).click()
    await page.getByRole('button', { name: 'All' }).click()
    await page.getByRole('option', { name: 'Error' }).click()

    await page.getByRole('button', { name: 'started_desc' }).click()
    await page.getByRole('option', { name: 'Oldest first' }).click()

    await page.getByRole('button', { name: '50' }).click()
    await page.getByRole('option', { name: '100' }).click()

    await expect(page).toHaveURL(
      /\/console\/realtime\?(?=.*status=error)(?=.*sort=started_asc)(?=.*limit=100)/
    )
    await expect(page.getByText('Showing 2 sessions')).toBeVisible()
  })
})
