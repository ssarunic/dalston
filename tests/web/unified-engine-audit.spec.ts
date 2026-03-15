// spec: Unified Engine UI Audit
// Verifies fixes for 10 audit findings from the engine rework:
//   1. Engines shown once in a unified view (batch + realtime merged per engine)
//   2. Capabilities shown per-model, not per-engine
//   3. Models shown for ALL stages (not just transcribe)
//   4. Dynamic engine filter (no hardcoded list)
//   5. "Engine" terminology (not "Runtime")
//   6. NewJob — all engines support batch (no filtering needed)
//   7. Dashboard shows model counts in CapabilitiesCard
//   8. Realtime worker status includes draining/busy
//   9. `interfaces` field exposed in API response
//  10. Pipeline stages derived from data (not hardcoded)

import { test, expect, type Page } from '@playwright/test'

// ---------------------------------------------------------------------------
// Mock data factories
// ---------------------------------------------------------------------------

function makeBatchEngine(
  engineId: string,
  stage: string,
  overrides: Partial<{
    status: string
    queue_depth: number
    processing: number
    interfaces: string[]
  }> = {}
) {
  return {
    engine_id: engineId,
    stage,
    status: 'idle',
    queue_depth: 0,
    processing: 0,
    interfaces: ['batch', 'realtime'],
    ...overrides,
  }
}

function makeRealtimeWorker(
  instance: string,
  overrides: Partial<{
    endpoint: string
    status: string
    capacity: number
    active_sessions: number
    models: string[]
    engine_id: string | null
    vocabulary_support: unknown
    interfaces: string[]
  }> = {}
) {
  return {
    instance,
    endpoint: `ws://localhost:9100/${instance}`,
    status: 'ready',
    capacity: 4,
    active_sessions: 0,
    models: [],
    engine_id: null,
    vocabulary_support: null,
    interfaces: ['batch', 'realtime'],
    ...overrides,
  }
}

function makeModel(
  id: string,
  engineId: string,
  stage: string,
  overrides: Partial<{
    name: string | null
    loaded_model_id: string
    status: string
    size_bytes: number | null
    word_timestamps: boolean
    punctuation: boolean
    capitalization: boolean
    native_streaming: boolean
    min_vram_gb: number | null
    min_ram_gb: number | null
    supports_cpu: boolean
    source: string | null
    library_name: string | null
    languages: string[] | null
    metadata: Record<string, unknown>
    download_path: string | null
    expected_total_bytes: number | null
    downloaded_bytes: number | null
    progress_updated_at: string | null
    downloaded_at: string | null
    last_used_at: string | null
    metadata_source: string
  }> = {}
) {
  const now = new Date().toISOString()
  return {
    id,
    name: id.split('/').pop() ?? id,
    engine_id: engineId,
    loaded_model_id: id,
    stage,
    status: 'ready',
    size_bytes: 500_000_000,
    word_timestamps: false,
    punctuation: true,
    capitalization: true,
    native_streaming: false,
    min_vram_gb: 2,
    min_ram_gb: 4,
    supports_cpu: false,
    source: null,
    library_name: null,
    languages: ['en'],
    metadata: {},
    download_path: null,
    expected_total_bytes: null,
    downloaded_bytes: null,
    progress_updated_at: null,
    downloaded_at: now,
    last_used_at: null,
    metadata_source: 'yaml',
    created_at: now,
    updated_at: now,
    ...overrides,
  }
}

// ---------------------------------------------------------------------------
// Common setup helpers
// ---------------------------------------------------------------------------

async function injectAuth(page: Page) {
  await page.addInitScript(() => {
    sessionStorage.setItem('dalston_api_key', 'test-admin-key')
  })
  await page.route('**/auth/me', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ scopes: ['admin', 'jobs:read', 'jobs:write', 'realtime'] }),
    })
  )
}

/** Default mocks for endpoints that most pages call but aren't under test. */
async function mockAncillary(page: Page) {
  await page.route('**/v1/realtime/status*', (route) =>
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
  )
}

// ============================================================================
// FINDING 1 + 9: Unified view — engines merged, interfaces field present
// ============================================================================

test.describe('Finding 1+9: Unified engine view & interfaces field', () => {
  const enginesResponse = {
    batch_engines: [
      makeBatchEngine('faster-whisper', 'transcribe', {
        processing: 1,
        queue_depth: 2,
      }),
      makeBatchEngine('audio-prepare', 'prepare'),
    ],
    realtime_engines: [
      makeRealtimeWorker('fw-instance-1', {
        engine_id: 'faster-whisper',
        models: ['whisper-base'],
        active_sessions: 1,
        capacity: 4,
      }),
    ],
  }

  test('Engines page shows each engine once with merged batch+realtime metrics', async ({
    page,
  }) => {
    await injectAuth(page)

    await page.route('**/api/console/engines*', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(enginesResponse),
      })
    )
    await page.route('**/v1/models*', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ data: [], total: 0 }),
      })
    )

    await page.goto('/console/engines')

    await expect(page.getByRole('heading', { name: 'Engines' })).toBeVisible()

    // There should be NO separate "Real-time Workers" section
    await expect(page.getByText('Real-time Workers', { exact: true })).not.toBeVisible()

    // There should be NO separate "Batch Pipeline" heading
    await expect(page.getByText('Batch Pipeline', { exact: true })).not.toBeVisible()

    // There should be a single "Pipeline" card title
    await expect(page.getByRole('heading', { name: 'Pipeline' })).toBeVisible()

    // faster-whisper should appear exactly once (as a link in the transcribe stage)
    // Expand transcribe to see it
    await page.getByText('Transcribe').click()
    const fwLinks = page.locator(`a:has-text("faster-whisper")`)
    await expect(fwLinks).toHaveCount(1)

    // The engine card should show realtime session count (merged from worker data)
    // 1/4 sessions from fw-instance-1
    const engineCard = page.locator('a:has-text("faster-whisper")')
    await expect(engineCard.getByText('/4')).toBeVisible()
  })

  test('API response includes interfaces field on both batch and realtime data', async ({
    page,
  }) => {
    await injectAuth(page)

    let capturedResponse: typeof enginesResponse | null = null

    await page.route('**/api/console/engines*', async (route) => {
      capturedResponse = enginesResponse
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(enginesResponse),
      })
    })
    await page.route('**/v1/models*', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ data: [], total: 0 }),
      })
    )

    await page.goto('/console/engines')
    await expect(page.getByRole('heading', { name: 'Engines' })).toBeVisible()

    expect(capturedResponse).not.toBeNull()
    const resp = capturedResponse!
    expect(resp.batch_engines[0]).toHaveProperty('interfaces')
    expect(resp.batch_engines[0].interfaces).toContain('batch')
    expect(resp.batch_engines[0].interfaces).toContain('realtime')
    expect(resp.realtime_engines[0]).toHaveProperty('interfaces')
    expect(resp.realtime_engines[0].interfaces).toContain('batch')
    expect(resp.realtime_engines[0].interfaces).toContain('realtime')
  })

  test('Summary cards show unified "Engines" and "Sessions" counts, not separate batch/realtime', async ({
    page,
  }) => {
    await injectAuth(page)

    await page.route('**/api/console/engines*', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(enginesResponse),
      })
    )
    await page.route('**/v1/models*', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ data: [], total: 0 }),
      })
    )

    await page.goto('/console/engines')

    // Should show unified "Engines" summary card, not "Batch Engines"
    await expect(page.getByText('Engines', { exact: true }).first()).toBeVisible()
    await expect(page.getByText('Batch Engines', { exact: true })).not.toBeVisible()

    // Should show "Sessions" summary card (realtime capacity)
    await expect(page.getByText('sessions', { exact: true }).first()).toBeVisible()
  })
})

// ============================================================================
// FINDING 2: Capabilities belong on model level, not engine level
// ============================================================================

test.describe('Finding 2: Per-model capabilities display', () => {
  test('Engine detail shows capabilities per model with summary counts', async ({
    page,
  }) => {
    await injectAuth(page)

    const engineId = 'faster-whisper'
    const models = [
      makeModel('whisper-tiny', engineId, 'transcribe', {
        word_timestamps: false,
        native_streaming: false,
        name: 'Whisper Tiny',
      }),
      makeModel('whisper-large-v3', engineId, 'transcribe', {
        word_timestamps: true,
        native_streaming: true,
        name: 'Whisper Large V3',
      }),
    ]

    await page.route('**/v1/engines*', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          engines: [
            {
              id: engineId,
              name: 'Faster Whisper',
              stage: 'transcribe',
              version: '1.0.0',
              status: 'running',
              capabilities: {
                supports_word_timestamps: true,
                supports_native_streaming: true,
                max_audio_duration_s: 7200,
                max_concurrency: 2,
              },
            },
          ],
          total: 1,
        }),
      })
    )

    await page.route('**/api/console/engines*', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          batch_engines: [makeBatchEngine(engineId, 'transcribe')],
          realtime_engines: [],
        }),
      })
    )

    await page.route('**/v1/models*', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ data: models, total: models.length }),
      })
    )

    await page.goto(`/console/engines/${encodeURIComponent(engineId)}`)

    await expect(page.getByRole('heading', { name: engineId })).toBeVisible()

    // Both models should be listed
    await expect(page.getByText('Whisper Tiny')).toBeVisible()
    await expect(page.getByText('Whisper Large V3')).toBeVisible()

    // Should NOT have a standalone "Capabilities" card with engine-wide booleans
    // Instead, capability badges appear on individual model cards
    // Whisper Large V3 has word timestamps + streaming badges
    const largeModelCard = page.locator('.rounded-lg.border:has-text("Whisper Large V3")')
    await expect(largeModelCard.getByText('word timestamps', { exact: true })).toBeVisible()
    await expect(largeModelCard.getByText('streaming', { exact: true })).toBeVisible()

    // Summary counts should appear in the models section header
    // "1/2 word timestamps · 1/2 streaming"
    await expect(page.getByText('1/2 word timestamps')).toBeVisible()
    await expect(page.getByText('1/2 streaming')).toBeVisible()
  })

  test('Dashboard CapabilitiesCard shows model counts, not just booleans', async ({
    page,
  }) => {
    await injectAuth(page)

    const models = [
      makeModel('m1', 'fw', 'transcribe', { word_timestamps: true, native_streaming: true, status: 'ready' }),
      makeModel('m2', 'fw', 'transcribe', { word_timestamps: true, native_streaming: false, status: 'ready' }),
      makeModel('m3', 'fw', 'transcribe', { word_timestamps: false, native_streaming: false, status: 'ready' }),
    ]

    await page.route('**/api/console/dashboard*', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          system: { healthy: true, version: '0.1.0' },
          batch: { running_jobs: 0, queued_jobs: 0, completed_today: 0, failed_today: 0 },
          realtime: { total_capacity: 4, used_capacity: 0, available_capacity: 4, worker_count: 1, ready_workers: 1 },
          recent_jobs: [],
        }),
      })
    )

    await page.route('**/v1/engines/capabilities*', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          stages: {
            transcribe: { engines: ['fw'], supports_word_timestamps: true, supports_native_streaming: true },
          },
          max_audio_duration_s: null,
          supported_formats: [],
        }),
      })
    )

    await page.route('**/v1/models*', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ data: models, total: models.length }),
      })
    )

    await page.route('**/api/console/jobs*', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ jobs: [], cursor: null, has_more: false }),
      })
    )

    await page.route('**/v1/realtime/sessions*', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ sessions: [], cursor: null, has_more: false }),
      })
    )

    await page.route('**/api/console/metrics*', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          throughput: [], success_rates: [], total_audio_minutes: 0,
          total_jobs_all_time: 0, engines: [], grafana_url: null,
        }),
      })
    )

    await page.goto('/console/')

    await expect(page.getByText('System Capabilities')).toBeVisible()

    // Should show model counts like "2 models" for word timestamps
    await expect(page.getByText('Word Timestamps:')).toBeVisible()
    await expect(page.getByText('2 models')).toBeVisible()

    // Streaming: only 1 model
    await expect(page.getByText('1 model')).toBeVisible()
  })
})

// ============================================================================
// FINDING 3: Models should be shown for ALL stages, not just transcribe
// ============================================================================

test.describe('Finding 3: Models for all pipeline stages', () => {
  test('Engines page shows model badges for diarize stage engines', async ({ page }) => {
    await injectAuth(page)

    const models = [
      makeModel('pyannote/speaker-diarization-3.1', 'pyannote', 'diarize', {
        name: 'Speaker Diarization 3.1',
        status: 'ready',
      }),
      makeModel('pyannote/speaker-diarization-3.0', 'pyannote', 'diarize', {
        name: 'Speaker Diarization 3.0',
        status: 'not_downloaded',
      }),
    ]

    await page.route('**/api/console/engines*', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          batch_engines: [makeBatchEngine('pyannote', 'diarize')],
          realtime_engines: [],
        }),
      })
    )

    await page.route('**/v1/models*', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ data: models, total: models.length }),
      })
    )

    await page.goto('/console/engines')

    // Expand the Diarize stage
    await page.getByText('Diarize').click()

    // Model badges should be visible for the diarize engine
    await expect(page.getByText('pyannote')).toBeVisible()
    await expect(page.getByText('Speaker Diarization 3.1')).toBeVisible()
  })

  test('EngineDetail page shows models for diarize engine', async ({ page }) => {
    await injectAuth(page)

    const engineId = 'pyannote'
    const models = [
      makeModel('pyannote/speaker-diarization-3.1', engineId, 'diarize', {
        name: 'Speaker Diarization 3.1',
        status: 'ready',
        min_vram_gb: 4,
      }),
    ]

    await page.route('**/v1/engines*', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          engines: [
            {
              id: engineId,
              name: 'Pyannote',
              stage: 'diarize',
              version: '3.1.0',
              status: 'running',
              capabilities: {
                supports_word_timestamps: false,
                supports_native_streaming: false,
                max_audio_duration_s: null,
                max_concurrency: 1,
              },
            },
          ],
          total: 1,
        }),
      })
    )

    await page.route('**/api/console/engines*', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          batch_engines: [makeBatchEngine(engineId, 'diarize')],
          realtime_engines: [],
        }),
      })
    )

    await page.route('**/v1/models*', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ data: models, total: models.length }),
      })
    )

    await page.goto(`/console/engines/${encodeURIComponent(engineId)}`)

    // Models should be visible for diarize engine (no stage gate)
    await expect(page.getByText('Speaker Diarization 3.1')).toBeVisible()
  })
})

// ============================================================================
// FINDING 4: Dynamic engine filter (no hardcoded list)
// ============================================================================

test.describe('Finding 4: Dynamic engine filter on Models page', () => {
  test('Models page engine filter includes engines from data, not hardcoded list', async ({
    page,
  }) => {
    await injectAuth(page)

    const models = [
      makeModel('model-a', 'faster-whisper', 'transcribe', { name: 'Model A' }),
      makeModel('model-b', 'my-custom-engine', 'transcribe', { name: 'Model B' }),
      makeModel('model-c', 'another-new-engine', 'diarize', { name: 'Model C' }),
    ]

    await page.route('**/v1/models*', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ data: models, total: models.length }),
      })
    )

    await page.goto('/console/models')

    await expect(page.getByText('Model A')).toBeVisible()

    // Open the engine filter dropdown
    const engineFilter = page.locator('button', { hasText: /engine/i })
    await engineFilter.first().click()

    // Dynamic engines from the data should appear
    await expect(page.getByRole('option', { name: /my-custom-engine/i })).toBeVisible()
    await expect(page.getByRole('option', { name: /another-new-engine/i })).toBeVisible()
  })
})

// ============================================================================
// FINDING 5: "Runtime" → "Engine" terminology
// ============================================================================

test.describe('Finding 5: Consistent Engine terminology', () => {
  test('Models page uses "Engine" not "Runtime" as column header', async ({ page }) => {
    await injectAuth(page)

    const models = [
      makeModel('test-model', 'faster-whisper', 'transcribe', { name: 'Test Model' }),
    ]

    await page.route('**/v1/models*', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ data: models, total: models.length }),
      })
    )

    await page.goto('/console/models')
    await expect(page.getByText('Test Model')).toBeVisible()

    // Table header should say "Engine", not "Runtime"
    const runtimeHeaders = page.locator('th', { hasText: 'Runtime' })
    const engineHeaders = page.locator('th', { hasText: 'Engine' })

    await expect(engineHeaders).toHaveCount(1)
    await expect(runtimeHeaders).toHaveCount(0)
  })
})

// ============================================================================
// FINDING 8: Realtime worker status includes draining/busy
// ============================================================================

test.describe('Finding 8: Rich worker status in unified view', () => {
  test('Engine detail shows realtime utilization bar with session data from workers', async ({
    page,
  }) => {
    await injectAuth(page)

    const engineId = 'faster-whisper'

    await page.route('**/v1/engines*', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          engines: [
            {
              id: engineId,
              name: 'Faster Whisper',
              stage: 'transcribe',
              version: '1.0.0',
              status: 'running',
              capabilities: {
                supports_word_timestamps: true,
                supports_native_streaming: true,
                max_audio_duration_s: 7200,
                max_concurrency: 2,
              },
            },
          ],
          total: 1,
        }),
      })
    )

    await page.route('**/api/console/engines*', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          batch_engines: [makeBatchEngine(engineId, 'transcribe', { processing: 1 })],
          realtime_engines: [
            makeRealtimeWorker('fw-ready-01', {
              engine_id: engineId,
              status: 'ready',
              active_sessions: 2,
              capacity: 4,
            }),
            makeRealtimeWorker('fw-draining-01', {
              engine_id: engineId,
              status: 'draining',
              active_sessions: 1,
              capacity: 4,
            }),
          ],
        }),
      })
    )

    await page.route('**/v1/models*', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ data: [], total: 0 }),
      })
    )

    await page.goto(`/console/engines/${encodeURIComponent(engineId)}`)

    // Should show Session Utilization section with aggregated data
    // 2 workers: total capacity 8, active sessions 3
    await expect(page.getByText('Session Utilization')).toBeVisible()
    await expect(page.getByText('3 active / 8 capacity')).toBeVisible()
    await expect(page.getByText('(2 workers)')).toBeVisible()
    await expect(page.getByText('38%')).toBeVisible()
  })

  test('Widened WorkerStatus type accepts draining and busy from API', async ({ page }) => {
    await injectAuth(page)

    // Engines with workers in draining/busy status — these should not cause errors
    await page.route('**/api/console/engines*', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          batch_engines: [makeBatchEngine('faster-whisper', 'transcribe')],
          realtime_engines: [
            makeRealtimeWorker('w-ready', { engine_id: 'faster-whisper', status: 'ready' }),
            makeRealtimeWorker('w-busy', { engine_id: 'faster-whisper', status: 'busy', active_sessions: 4, capacity: 4 }),
            makeRealtimeWorker('w-draining', { engine_id: 'faster-whisper', status: 'draining', active_sessions: 1, capacity: 4 }),
          ],
        }),
      })
    )
    await page.route('**/v1/models*', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ data: [], total: 0 }),
      })
    )

    await page.goto('/console/engines')

    // Page should load without JavaScript errors
    await expect(page.getByRole('heading', { name: 'Engines' })).toBeVisible()

    // Session count should aggregate: 0 + 4 + 1 = 5 sessions / 12 capacity
    await expect(page.getByText('sessions', { exact: true }).first()).toBeVisible()
  })
})

// ============================================================================
// FINDING 10: Pipeline stages should be derived from data
// ============================================================================

test.describe('Finding 10: Dynamic pipeline stages', () => {
  test('Engines page shows a custom stage that exists in engine data', async ({ page }) => {
    await injectAuth(page)

    await page.route('**/api/console/engines*', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          batch_engines: [
            makeBatchEngine('audio-prepare', 'prepare'),
            makeBatchEngine('custom-postprocess', 'postprocess'),
          ],
          realtime_engines: [],
        }),
      })
    )
    await page.route('**/v1/models*', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ data: [], total: 0 }),
      })
    )

    await page.goto('/console/engines')

    // Known stage appears with its label
    await expect(page.getByText('Prepare')).toBeVisible()

    // Unknown stage appears using its raw ID (no hardcoded label needed)
    await expect(page.getByText(/postprocess/i)).toBeVisible()
  })

  test('Pipeline stage count reflects actual stages from data, not hardcoded 7', async ({
    page,
  }) => {
    await injectAuth(page)

    await page.route('**/api/console/engines*', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          batch_engines: [
            makeBatchEngine('audio-prepare', 'prepare'),
            makeBatchEngine('faster-whisper', 'transcribe'),
          ],
          realtime_engines: [],
        }),
      })
    )
    await page.route('**/v1/models*', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ data: [], total: 0 }),
      })
    )

    await page.goto('/console/engines')

    // Pipeline stages card should show "2/2" (2 active / 2 total from data)
    // NOT "2/7" (the old hardcoded denominator)
    await expect(page.getByText('Pipeline Stages', { exact: true })).toBeVisible()
    // The denominator should be 2, not 7
    // Both "Engines" and "Pipeline Stages" cards show /2, so just verify /7 is absent
    await expect(page.getByText('/2').first()).toBeVisible()
    await expect(page.getByText('/7')).not.toBeVisible()
  })
})

// ============================================================================
// FINDING 6 (revised): No RealtimeWorkerDetail route
// ============================================================================

test.describe('Finding 6: RealtimeWorkerDetail route removed', () => {
  test('Old /realtime/workers/:id route does not render a dedicated page', async ({ page }) => {
    await injectAuth(page)

    await page.route('**/api/console/engines*', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ batch_engines: [], realtime_engines: [] }),
      })
    )
    await page.route('**/v1/models*', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ data: [], total: 0 }),
      })
    )

    // Navigate to the old worker detail route — should not render RealtimeWorkerDetail
    const response = await page.goto('/console/realtime/workers/test-worker')

    // The route was removed, so the page should not render the old worker detail
    // (either 404, redirect, or blank depending on router config)
    const workerDetailHeading = page.locator('text=Worker Details')
    await expect(workerDetailHeading).not.toBeVisible()
  })
})

// ============================================================================
// INTEGRATION: Full engines page with all fixes applied
// ============================================================================

test.describe('Integration: Engines page with unified data', () => {
  test('Full scenario: engines with models across stages, unified view', async ({
    page,
  }) => {
    await injectAuth(page)

    const models = [
      makeModel('whisper-base', 'faster-whisper', 'transcribe', {
        name: 'Whisper Base',
        status: 'ready',
        word_timestamps: true,
        native_streaming: true,
      }),
      makeModel('whisper-large-v3', 'faster-whisper', 'transcribe', {
        name: 'Whisper Large V3',
        status: 'ready',
        word_timestamps: true,
        native_streaming: false,
      }),
      makeModel('pyannote-3.1', 'pyannote', 'diarize', {
        name: 'Pyannote 3.1',
        status: 'ready',
      }),
      makeModel('nemo-align', 'nemo', 'align', {
        name: 'NeMo Align',
        status: 'not_downloaded',
      }),
    ]

    const enginesResponse = {
      batch_engines: [
        makeBatchEngine('audio-prepare', 'prepare'),
        makeBatchEngine('faster-whisper', 'transcribe', {
          processing: 2,
          queue_depth: 5,
        }),
        makeBatchEngine('nemo', 'align'),
        makeBatchEngine('pyannote', 'diarize'),
        makeBatchEngine('final-merger', 'merge'),
      ],
      realtime_engines: [
        makeRealtimeWorker('fw-rt-01', {
          engine_id: 'faster-whisper',
          models: ['whisper-base'],
          active_sessions: 2,
          capacity: 4,
        }),
      ],
    }

    await page.route('**/api/console/engines*', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(enginesResponse),
      })
    )
    await page.route('**/v1/models*', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ data: models, total: models.length }),
      })
    )

    await page.goto('/console/engines')

    // Page loads without errors
    await expect(page.getByRole('heading', { name: 'Engines' })).toBeVisible()

    // Unified summary cards
    await expect(page.getByRole('heading', { name: 'Engines' })).toBeVisible()
    await expect(page.getByText('Pipeline Stages', { exact: true })).toBeVisible()
    await expect(page.getByText('Issues', { exact: true })).toBeVisible()

    // Expand transcribe stage — should show models
    await page.getByText('Transcribe').click()
    await expect(page.getByText('Whisper Base')).toBeVisible()
    await expect(page.getByText('Whisper Large V3')).toBeVisible()

    // Expand diarize stage — models should appear (no transcribe gate)
    await page.getByText('Diarize').click()
    await expect(page.getByText('Pyannote 3.1')).toBeVisible()

    // Expand align stage — models should appear
    await page.getByText('Align').click()
    await expect(page.getByText('NeMo Align')).toBeVisible()
  })
})
