# Unified Engine UI Rework — Implementation Plan

## Overview

All engines are unified — every engine supports both batch and realtime interfaces.
Runtimes that don't natively support realtime use a thin wrapper that splits audio
on silence (or max chunk length) to simulate streaming. The web console must stop
treating batch and realtime as separate worlds and present a single unified view.

This plan addresses 10 audit findings across 4 phases.

---

## Phase 1: Backend + Types — Expose `interfaces` and merge response models

### 1A. Add `interfaces` to console API response models

**File:** `dalston/gateway/api/console.py`

- Add `interfaces: list[str]` field to `BatchEngine` Pydantic model (always `["batch", "realtime"]`).
- Add `interfaces: list[str]` field to `RealtimeWorker` Pydantic model (always `["batch", "realtime"]`).
- In `get_engines()`, populate `interfaces` from heartbeat data (`data.get("interfaces")`).
- For catalog-only offline entries, default to `["batch", "realtime"]`.

### 1B. Widen `RealtimeWorker.status`

**File:** `dalston/gateway/api/console.py`

- Change `RealtimeWorker.status` from `ready | unhealthy` to
  `Literal["ready", "busy", "draining", "offline", "unhealthy"]`.
- The session router already tracks richer status; stop discarding it.

### 1C. Update TypeScript types

**File:** `web/src/api/types.ts`

- Add `interfaces: string[]` to `BatchEngine`.
- Add `interfaces: string[]` to `WorkerStatus`.
- Widen `WorkerStatus.status` to `'ready' | 'busy' | 'draining' | 'offline' | 'unhealthy'`.

---

## Phase 2: Engines page — Single unified view

### 2A. Merge batch + realtime into one Engines page

**File:** `web/src/pages/Engines.tsx`

**Current:** Two hard-separated sections — "Batch Pipeline" and "Real-time Workers".

**New:** One unified view. The stage accordion stays (engines grouped by stage),
but each engine card shows **both** batch metrics (queue depth, processing) and
realtime metrics (active sessions, capacity, utilization). No separate realtime
section — every engine appears once under its stage.

**Logic change:**

- Match `batch_engines` and `realtime_engines` by `engine_id` to build a
  single engine list with merged metrics.
- Summary cards: "Engines: X/Y healthy", "Stages: N active" — no batch/realtime split.

### 2B. Show models for ALL stages (not just transcribe)

**Files:** `web/src/pages/Engines.tsx`, `web/src/pages/EngineDetail.tsx`

- Remove the `stage === 'transcribe'` guard from model badge rendering in
  `Engines.tsx` (~line 194) and from the models section in `EngineDetail.tsx`.
- Query `useModelRegistry({ engine_id })` for any engine, regardless of stage.
- Backend already supports `GET /v1/models?engine_id=<any>` — no change needed.

### 2C. Single engine detail page

**File:** `web/src/pages/EngineDetail.tsx`

- Always render both batch metrics (queue depth, processing count) and realtime
  metrics (active sessions, capacity, utilization bar) on the same page.
- Merge data from `batch_engines` and `realtime_engines` by `engine_id`.
- Remove the separate `RealtimeWorkerDetail.tsx` page — redirect its route
  to `EngineDetail`.

**File:** `web/src/pages/RealtimeWorkerDetail.tsx` — **Delete**.

**File:** `web/src/App.tsx`

- Remove `/realtime/workers/:workerId` route (or redirect to `/engines/:engineId`).

### 2D. Dynamic pipeline stages

**File:** `web/src/pages/Engines.tsx`

- Replace the hardcoded `PIPELINE_STAGES` array.
- Derive stages from actual engine data: `[...new Set(engines.map(e => e.stage))]`.
- Keep an ordered list as a sort-order hint, but show any stage that exists
  in the data even if not in the hint list.

---

## Phase 3: Model filtering + terminology

### 3A. Dynamic engine filter

**File:** `web/src/components/ModelFiltersBar.tsx`

- Replace the hardcoded `RUNTIMES` array with a dynamic list derived from
  distinct `engine_id` values in the model registry response.
- Sort alphabetically.

### 3B. Rename "Runtime" → "Engine"

**Files:**

- `web/src/components/ModelTable.tsx` — column header
- `web/src/components/ModelFiltersBar.tsx` — filter label

Change "Runtime" to "Engine" everywhere in the Models page.

### 3C. Capabilities per-model (not per-engine)

**File:** `web/src/pages/EngineDetail.tsx`

- Remove the engine-level "Capabilities" card that shows `supports_word_timestamps`
  and `supports_native_streaming` as engine-wide booleans.
- Show capabilities **per model** in the Available Models grid — each model card
  already shows badges; add a summary: "3/5 models support word timestamps".

### 3D. Dashboard CapabilitiesCard — show model counts

**File:** `web/src/components/CapabilitiesCard.tsx`

- Change from boolean checkmarks to counts:
  "Word Timestamps: 8 models" / "Streaming: 3 models".
- Compute client-side from the model registry.

---

## Phase 4: Dashboard + cross-cutting

### 4A. Engine utilization on Dashboard

**File:** `web/src/pages/Dashboard.tsx`

- Replace the separate batch/realtime summary cards with a unified
  "Engine Utilization" card showing:
  - Total engines, healthy count
  - Combined utilization: batch queue depth + realtime active sessions
  - Per-engine utilization breakdown (optional, if space allows)

### 4B. Engine badge on recent activity

**File:** `web/src/pages/Dashboard.tsx`

- On recent jobs and recent sessions, show which engine processed each item
  as a small badge/link.

---

## Change Summary by File

| File | Changes |
|------|---------|
| `dalston/gateway/api/console.py` | Add `interfaces`; widen status |
| `web/src/api/types.ts` | Add `interfaces`; widen WorkerStatus.status |
| `web/src/pages/Engines.tsx` | Merge batch+realtime; dynamic stages; models for all stages |
| `web/src/pages/EngineDetail.tsx` | Unified detail with both metrics; capabilities per-model |
| `web/src/pages/RealtimeWorkerDetail.tsx` | **Delete** |
| `web/src/App.tsx` | Remove/redirect realtime worker detail route |
| `web/src/components/CapabilitiesCard.tsx` | Model counts instead of booleans |
| `web/src/components/ModelFiltersBar.tsx` | Dynamic engines; rename "Runtime" → "Engine" |
| `web/src/components/ModelTable.tsx` | Rename "Runtime" → "Engine" column header |
| `web/src/pages/Dashboard.tsx` | Unified engine utilization; engine badges on activity |

---

## Implementation Order

1. **Phase 1** (Backend + types) — Must go first; frontend depends on `interfaces`.
2. **Phase 3A + 3B** (Dynamic filters, terminology) — Quick wins, no dependencies.
3. **Phase 2B** (Models for all stages) — Quick win, remove hardcoded guard.
4. **Phase 2A + 2D** (Merge Engines page, dynamic stages) — Core restructure.
5. **Phase 2C** (Unified detail page, delete RealtimeWorkerDetail) — Builds on 2A.
6. **Phase 3C + 3D** (Capabilities per-model) — Independent, moderate effort.
7. **Phase 4** (Dashboard) — Independent, can be done anytime after Phase 1.
