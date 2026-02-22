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

test.describe('Job Detail audio player', () => {
  test('segment seek does not auto-resume after pause and audio player does not leak across jobs', async ({ page }) => {
    const wavBuffer = makeSilentWav()
    const now = new Date().toISOString()
    const jobWithAudioId = 'job-with-audio-0000-0000-0000-000000000001'
    const jobPurgedId = 'job-purged-0000-0000-0000-000000000002'

    const baseJob = {
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
      retention: { mode: 'keep' },
      pii: { enabled: false, redacted_audio_available: false },
    }

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
          job_id: jobWithAudioId,
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

    await page.route('**/v1/audio/transcriptions/*/audio', async (route, request) => {
      const url = new URL(request.url())
      const jobId = url.pathname.split('/')[4]
      if (jobId === jobWithAudioId) {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            url: '/mock-audio.wav',
            expires_in: 3600,
            type: 'original',
          }),
        })
      } else {
        await route.fulfill({ status: 404, body: 'not found' })
      }
    })

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

      const jobId = path.split('/')[4]
      const isPurged = jobId === jobPurgedId
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          id: jobId,
          ...baseJob,
          retention: isPurged
            ? { mode: 'auto_delete', purged_at: now }
            : baseJob.retention,
        }),
      })
    })

    await page.goto(`/console/jobs/${jobWithAudioId}`)
    await expect(page.getByRole('button', { name: 'Download audio' })).toBeVisible()

    await page.getByText('Segment one text').first().click()
    await expect(page.getByLabel('Pause')).toBeVisible()
    await page.getByLabel('Pause').click()

    await page.getByText('Segment two text').first().click()
    await expect(page.getByLabel('Play')).toBeVisible()
    await page.waitForTimeout(1000)
    await expect(page.getByLabel('Play')).toBeVisible()

    await page.goto(`/console/jobs/${jobPurgedId}`)
    await expect(page.getByRole('button', { name: 'Download audio' })).toHaveCount(0)
  })
})
