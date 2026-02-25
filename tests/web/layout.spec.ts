// spec: 3-Zone Header Layout
// seed: tests/web/seed.spec.ts

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

test.describe('3-Zone Header Layout', () => {
  test('should render Transcript title and Export dropdown button in row 1', async ({ page }) => {
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

    // 1. Set up API key in sessionStorage and mock auth/jobs API endpoints
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

    // 2. Navigate to a mocked job detail page with completed status, audio, and transcript
    await page.goto(`http://localhost:3000/jobs/${jobId}`)

    // 3. Locate the Transcript section on the job detail page
    // Wait for the transcript card to render with the "Transcript" heading (Card title only)
    const transcriptHeading = page.getByRole('heading', { name: 'Transcript' })
    await expect(transcriptHeading).toBeVisible()

    // 4. Verify the first row contains an "Export" button with download icon and chevron on the right
    await expect(page.getByRole('button', { name: /Export/ })).toBeVisible()

    // 5. Verify the audio player row (row 3) contains skip back button (desktop viewport)
    await expect(page.getByRole('button', { name: 'Skip back 10 seconds' })).toBeVisible()

    // 5. Verify the audio player row contains skip forward button (desktop viewport)
    await expect(page.getByRole('button', { name: 'Skip forward 10 seconds' })).toBeVisible()

    // 5. Verify the audio player row contains auto-scroll toggle button
    await expect(page.getByRole('button', { name: 'Enable auto-scroll' })).toBeVisible()

    // 5. Verify the audio player row contains download audio button
    await expect(page.getByRole('button', { name: 'Download audio' })).toBeVisible()
  })
})
