import { test, expect } from '@playwright/test'

// Generate a mock job object
function makeJob(index: number, status: 'completed' | 'running' = 'completed') {
  return {
    id: `job-${String(index).padStart(8, '0')}-0000-0000-0000-000000000000`,
    status,
    created_at: new Date(Date.now() - index * 60_000).toISOString(),
    audio_duration_seconds: 120,
    result_language_code: 'en',
    result_word_count: 500,
  }
}

function makePageResponse(
  startIndex: number,
  count: number,
  hasMore: boolean,
  cursor: string | null
) {
  return {
    jobs: Array.from({ length: count }, (_, i) => makeJob(startIndex + i)),
    cursor,
    has_more: hasMore,
  }
}

test.describe('Batch Jobs', () => {
  test('Load More increases rows and status/sort/limit update URL', async ({ page }) => {
    // Set API key in sessionStorage before page loads
    await page.addInitScript(() => {
      sessionStorage.setItem('dalston_api_key', 'test-admin-key')
    })

    // Mock jobs endpoint with cursor pagination and status-filtered responses
    await page.route('**/api/console/jobs*', (route, request) => {
      const url = new URL(request.url())
      const cursor = url.searchParams.get('cursor')
      const status = url.searchParams.get('status')

      if (status === 'running') {
        route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            jobs: [makeJob(101, 'running'), makeJob(102, 'running')],
            cursor: null,
            has_more: false,
          }),
        })
        return
      }

      if (!cursor) {
        route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify(makePageResponse(1, 20, true, 'cursor-page-2')),
        })
      } else {
        route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify(makePageResponse(21, 20, false, null)),
        })
      }
    })

    // Navigate to Batch Jobs page
    await page.goto('/console/jobs')

    // Wait for Load More button to appear
    await expect(page.getByRole('button', { name: 'Load More' })).toBeVisible()
    await expect(page.getByText('Showing 20 jobs')).toBeVisible()

    // Click Load More
    await page.getByRole('button', { name: 'Load More' }).click()

    // Verify more rows are shown
    await expect(page.getByText('Showing 40 jobs')).toBeVisible()

    // Apply status filter and verify URL reflects selected filter
    await page.getByRole('button', { name: 'Filters' }).click()
    await page.getByRole('button', { name: 'All Statuses' }).click()
    await page.getByRole('option', { name: 'Running' }).click()
    await expect(page.getByText('Showing 2 jobs')).toBeVisible()

    // Change sort and limit controls and verify all params are URL-synced
    await page.getByRole('button', { name: 'created_desc' }).click()
    await page.getByRole('option', { name: 'Oldest first' }).click()

    await page.getByRole('button', { name: '20' }).click()
    await page.getByRole('option', { name: '50' }).click()

    await expect(page).toHaveURL(
      /\/console\/jobs\?(?=.*status=running)(?=.*sort=created_asc)(?=.*limit=50)/
    )
  })
})
