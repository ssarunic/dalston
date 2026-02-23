// spec: Mobile Responsive Overflow Menu
// seed: tests/web/layout.spec.ts

import { test, expect } from '@playwright/test'

function makeSilentWav(seconds = 4, sampleRate = 8000): Buffer {
  const channels = 1
  const bytesPerSample = 2
  const totalSamples = seconds * sampleRate
  const dataSize = totalSamples * channels * bytesPerSample
  const headerSize = 44
  const buffer = Buffer.alloc(headerSize + dataSize)

  buffer.write('RIFF', 0)
  buffer.writeUInt32LE(buffer.length - 8, 4)
  buffer.write('WAVE', 8)
  buffer.write('fmt ', 12)
  buffer.writeUInt32LE(16, 16)
  buffer.writeUInt16LE(1, 20)
  buffer.writeUInt16LE(channels, 22)
  buffer.writeUInt32LE(sampleRate, 24)
  buffer.writeUInt32LE(sampleRate * channels * bytesPerSample, 28)
  buffer.writeUInt16LE(channels * bytesPerSample, 32)
  buffer.writeUInt16LE(bytesPerSample * 8, 34)
  buffer.write('data', 36)
  buffer.writeUInt32LE(dataSize, 40)

  return buffer
}

test.describe('Mobile Responsive Overflow Menu', () => {
  test('should collapse skip buttons and secondary controls into overflow menu on narrow viewport', async ({ page }) => {
    const wavBuffer = makeSilentWav()
    const now = new Date().toISOString()
    const jobId = 'job-layout-test-0000-0000-000000000001'

    const mockJob = {
      id: jobId,
      status: 'completed',
      created_at: now,
      audio_duration_seconds: 4,
      result_language_code: 'en',
      result_word_count: 10,
      result_segment_count: 2,
      result_speaker_count: 1,
      text: 'Segment one text. Segment two text.',
      speakers: [{ id: 'spk-1', label: 'Speaker 1' }],
      segments: [
        {
          id: 'seg-1',
          start: 0,
          end: 2,
          text: 'Segment one text',
          speaker: 'spk-1',
          confidence: 0.99,
        },
        {
          id: 'seg-2',
          start: 2,
          end: 4,
          text: 'Segment two text',
          speaker: 'spk-1',
          confidence: 0.99,
        },
      ],
      retention: { mode: 'keep', scope: 'all' },
      pii: { enabled: false, redacted_audio_available: false },
    }

    // 1. Set viewport to 390x844 (iPhone 12 size, narrower than 640px breakpoint)
    await page.setViewportSize({ width: 390, height: 844 })

    // Set up API key in sessionStorage and mock auth/jobs API endpoints
    await page.addInitScript(() => {
      sessionStorage.setItem('dalston_api_key', 'test-admin-key')
    })

    await page.route('**/auth/me', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ scopes: ['admin', 'jobs:read'] }),
      })
    )

    await page.route('**/api/console/jobs/*/tasks', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          job_id: jobId,
          tasks: [
            {
              id: 'task-1',
              stage: 'transcribe',
              engine_id: 'mock',
              status: 'completed',
              dependencies: [],
            },
          ],
        }),
      })
    )

    await page.route('**/v1/audit/resources/job/*', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ events: [] }),
      })
    )

    await page.route('**/v1/audio/transcriptions/*/audio', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          url: '/mock-audio.wav',
          expires_in: 3600,
          type: 'original',
        }),
      })
    )

    await page.route('**/mock-audio.wav', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'audio/wav',
        body: wavBuffer,
      })
    )

    await page.route('**/v1/audio/transcriptions/*', async (route, request) => {
      const url = new URL(request.url())
      const path = url.pathname
      if (path.endsWith('/audio') || path.endsWith('/audio/redacted')) {
        await route.fallback()
        return
      }
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(mockJob),
      })
    })

    // 2. Navigate to the job detail page using the same mock setup from layout.spec.ts
    await page.goto(`http://localhost:3000/jobs/${jobId}`)

    // 3. Wait for the audio player to be ready (wait for the Transcript section to appear)
    await expect(page.getByRole('heading', { name: 'Transcript' }).first()).toBeVisible()

    // 4. Verify that the skip back button is NOT visible as a standalone button
    await expect(page.getByRole('button', { name: 'Skip back 10 seconds' })).not.toBeVisible()

    // 4. Verify that the skip forward button is NOT visible as a standalone button
    await expect(page.getByRole('button', { name: 'Skip forward 10 seconds' })).not.toBeVisible()

    // 4. Verify that the auto-scroll toggle button is NOT visible as a standalone button
    await expect(page.getByRole('button', { name: /auto-scroll/ })).not.toBeVisible()

    // 4. Verify that the download audio button is NOT visible as a standalone button
    await expect(page.getByRole('button', { name: 'Download audio' })).not.toBeVisible()

    // 4. Verify that the "More options" button (three dots) IS visible
    await expect(page.getByRole('button', { name: 'More options' })).toBeVisible()

    // 5. Click the "More options" button to open the overflow menu
    await page.getByRole('button', { name: 'More options' }).click()

    // 6. Verify the dropdown menu contains "Back 10s" item
    await expect(page.getByRole('menuitem', { name: /Back 10s/ })).toBeVisible()

    // 6. Verify the dropdown menu contains "Forward 10s" item
    await expect(page.getByRole('menuitem', { name: /Forward 10s/ })).toBeVisible()

    // 6. Verify the dropdown menu contains the auto-scroll toggle item
    await expect(page.getByRole('menuitem', { name: /auto-scroll/ })).toBeVisible()

    // 6. Verify the dropdown menu contains "Download audio" item
    await expect(page.getByRole('menuitem', { name: /Download audio/ })).toBeVisible()
  })
})
