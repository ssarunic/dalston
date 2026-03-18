# M74: HF Model Size + Download Progress Events

| | |
|---|---|
| **Goal** | Add accurate model-size discovery and live download progress visibility across API, web console, and CLI |
| **Duration** | 2-3 days (incremental, test-gated) |
| **Dependencies** | M40 (model registry), M42 (console model management), M13 (CLI) |
| **Primary Deliverable** | Persistent model download progress (bytes + percent) with optional Redis event push and client consumption |
| **Status** | In Progress — T1-T3, T5-T6 complete; T4 (Redis events) deferred, T7 (tests) partial |

## Background

Model downloads currently expose coarse status transitions (`not_downloaded`,
`downloading`, `ready`, `failed`) but do not persist incremental byte-level
progress. The API response includes `download_progress`, but it is not wired to
live values. Web and CLI users therefore cannot see where a long download is in
real time.

This milestone adds:

1. pre-download size estimation from Hugging Face;
2. periodic progress persistence during download;
3. client-visible progress in web console and CLI;
4. optional Redis events for push-based consumers.

## Outcomes

1. `GET /v1/models` and `GET /v1/models/{id}` return live download progress.
2. Model entries store both expected total size and current downloaded bytes.
3. Web console shows stable percent and byte progress during pulls.
4. CLI supports watch mode for live progress until terminal state.
5. Redis progress events are available for real-time subscribers (optional transport).

## Scope

In scope:

- DB schema and API contract for model download progress fields.
- HF size estimation using Hub metadata before pull starts.
- Throttled progress updates from downloader path.
- Web polling-based consumption of progress fields.
- CLI watch-mode consumption of progress fields.
- Optional Redis pub/sub event emission for progress.

Out of scope:

- Replacing polling with SSE/WebSocket as the initial delivery path.
- Guaranteed durable replay of progress events (pub/sub is sufficient for UX).
- Reworking download backend away from `snapshot_download`.

## Strategy

Roll out in two phases to minimize risk:

1. **Phase A (source of truth):** DB-backed progress + API + web/CLI polling.
2. **Phase B (optional push):** Redis progress events and push consumers.

This keeps progress visible quickly while avoiding transport complexity in the
first pass.

## Tactics

### T1. Schema and API Contract

Add persistent progress fields to model registry records and return them from
all model endpoints.

- Schema additions:
  - `expected_total_bytes` (`BIGINT`, nullable)
  - `downloaded_bytes` (`BIGINT`, nullable)
  - `progress_updated_at` (`TIMESTAMPTZ`, nullable)
- Keep `size_bytes` as final downloaded footprint.
- Wire `download_progress` to computed value when total is known.

Target files:

- `dalston/db/models.py`
- `alembic/versions/*`
- `dalston/gateway/api/v1/models.py`

Gate:

- Endpoints return non-null progress fields during download.

### T2. Hugging Face Size Estimation

Before download starts, call:

- `HfApi.model_info(model_id, files_metadata=True)`

Sum file sizes from `siblings[].size` and persist as `expected_total_bytes`.
If file metadata is incomplete, keep total nullable and continue with byte-only
progress.

Target files:

- `dalston/gateway/services/hf_resolver.py` (shared helper)
- `dalston/gateway/services/model_registry.py`

Gate:

- `expected_total_bytes` is set for common HF repos before first progress tick.

### T3. Downloader Progress Instrumentation

Instrument `snapshot_download` with a custom `tqdm_class` callback and throttle
writes (for example every 2 seconds or when bytes increase by threshold).

Persist on each tick:

- `downloaded_bytes`
- `progress_updated_at`
- derived `download_progress` (when total known)

On completion:

- mark `status=ready`
- set `size_bytes`
- clear/normalize transient progress fields as needed

On failure:

- mark `status=failed`
- preserve last known progress for debugging

Target file:

- `dalston/gateway/services/model_registry.py`

Gate:

- Progress advances during active download, not only at completion.

### T4. Optional Redis Progress Events

Publish lightweight events on progress ticks for real-time consumers:

- event type: `model.download.progress`
- payload: `model_id`, `downloaded_bytes`, `expected_total_bytes`,
  `progress_pct`, `speed_bps`, `status`, `timestamp`

Use pub/sub by default; durable stream not required for UI progress.

Target files:

- `dalston/common/events.py` (or dedicated model progress publisher helper)
- `dalston/gateway/services/model_registry.py`

Gate:

- Active pulls emit periodic events visible to subscribers.

### T5. Web Console Consumption

Reuse existing polling in model registry hooks and render live progress from API
fields (percent plus byte text where available).

Target files:

- `web/src/api/types.ts`
- `web/src/hooks/useModelRegistry.ts`
- `web/src/components/ModelTable.tsx`
- `web/src/components/ModelCard.tsx`

Gate:

- `/models` page reflects download movement within current polling interval.

### T6. CLI Consumption

Add a watch mode to model pull flow (`--watch`) that polls model status and
renders a progress bar until terminal state.

Target files:

- `cli/dalston_cli/commands/models.py`
- `cli/dalston_cli/bootstrap/model_manager.py` (shared polling style)

Gate:

- CLI shows live progress and exits with correct status on `ready`/`failed`.

### T7. Tests and Validation

Add coverage for:

- HF size aggregation and unknown-size fallback.
- Progress math and throttling behavior.
- API contract returning live progress fields.
- CLI watch behavior for success/failure paths.

Recommended gate:

```bash
pytest tests/unit
pytest tests/integration -k model
```

## Implementation Plan

### Phase 1: Data and API Foundation

1. Add schema fields + migration.
2. Return fields from model endpoints.
3. Keep compatibility for existing clients.

### Phase 2: Size Estimation + Progress Persistence

1. Estimate total bytes from HF metadata.
2. Add downloader progress callbacks.
3. Persist throttled updates in DB.

### Phase 3: Client Surfaces

1. Web: render bytes/percent from polling responses.
2. CLI: add `--watch` progress mode.

### Phase 4: Optional Push Channel

1. Publish Redis progress events.
2. Add push consumers only where needed.

## Success Criteria

1. Users can observe download progression in API, web, and CLI without reading logs.
2. Progress values are monotonic and update at bounded cadence.
3. Final model state remains correct (`ready`/`failed`) with no regressions in pull flow.
4. Existing model-management permissions and audit behavior remain unchanged.

## Risks and Caveats

- HF metadata may not provide complete file-size totals for every repo.
- Parallel/resumed downloads can create non-linear progress; UI should tolerate jumps.
- Overly frequent writes can increase DB load; throttling is required.
- Redis pub/sub events are transient; DB remains source of truth.

## References

- `dalston/gateway/services/model_registry.py`
- `dalston/gateway/services/hf_resolver.py`
- `dalston/gateway/api/v1/models.py`
- `dalston/db/models.py`
- `web/src/hooks/useModelRegistry.ts`
- `cli/dalston_cli/commands/models.py`
