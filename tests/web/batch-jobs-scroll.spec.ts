import { test, expect } from '@playwright/test'

// Generate a mock job object
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
  test('Load More does not scroll to top', async ({ page }) => {
    // Set API key in sessionStorage before page loads
    await page.addInitScript(() => {
      sessionStorage.setItem('dalston_api_key', 'test-admin-key')
    })

    // Mock auth endpoint
    await page.route('**/auth/me', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ scopes: ['admin', 'jobs:read'] }),
      })
    )

    // Mock jobs endpoint - first page returns 20 jobs with has_more: true
    await page.route('**/api/console/jobs*', (route, request) => {
      const url = new URL(request.url())
      const cursor = url.searchParams.get('cursor')

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

    // Scroll to bottom of the page
    const scrollContainer = page.locator('main')
    await scrollContainer.evaluate((el) =>
      el.scrollTo({ top: el.scrollHeight, behavior: 'instant' })
    )

    // Capture scroll position before clicking Load More
    const scrollBefore = await scrollContainer.evaluate((el) => el.scrollTop)
    expect(scrollBefore).toBeGreaterThan(0)

    // Click Load More
    await page.getByRole('button', { name: 'Load More' }).click()

    // Wait for second page to load
    await expect(page.getByText('Showing 40 jobs')).toBeVisible()

    // Verify scroll position did NOT reset to top
    const scrollAfter = await scrollContainer.evaluate((el) => el.scrollTop)
    expect(scrollAfter).toBeGreaterThan(0)
  })
})
