# Unified Engine UI Rework — Implementation Plan

## Overview

The backend already supports unified engines (via `EngineRecord.interfaces: list[str]`),
but the web console still treats batch and realtime as two separate worlds.
This plan addresses all 10 audit findings.

---

## Phase 1: Backend — Expose `interfaces` to the frontend

### 1A. Add `interfaces` to console API response models

**File:** `dalston/gateway/api/console.py`

- Add `interfaces: list[str]` field to `BatchEngine` Pydantic model.
- Add `interfaces: list[str]` field to `RealtimeWorker` Pydantic model.
- In `get_engines()`, populate `interfaces` from the heartbeat data
  (already available as `data.get("interfaces")`).
- For catalog-only offline entries (no heartbeat), infer interfaces:
  - Batch catalog entries (have stages): `["batch"]`
  - Realtime catalog entries (no stages): `["realtime"]`

### 1B. Widen `RealtimeWorker.status` to match `EngineRecord`

**File:** `dalston/gateway/api/console.py`

- Change `RealtimeWorker.status` from implicit `ready | unhealthy` to a
  `Literal["ready", "busy", "draining", "offline", "unhealthy"]`.
- The session router already tracks richer status; just stop discarding it.

### 1C. Add `interfaces` to TypeScript types

**File:** `web/src/api/types.ts`

- Add `interfaces: string[]` to `BatchEngine`.
- Add `interfaces: string[]` to `WorkerStatus`.
- Widen `WorkerStatus.status` to `'ready' | 'busy' | 'draining' | 'offline' | 'unhealthy'`.

---

## Phase 2: Engines page — Unified engine view

### 2A. Restructure the Engines page layout

**File:** `web/src/pages/Engines.tsx`

**Current:** Two hard-separated sections — "Batch Pipeline" and "Real-time Workers".

**New:** Three sections:
1. **Pipeline Overview** — The stage accordion stays but gains awareness of
   unified engines. Each stage card shows engines that have `"batch"` in their
   `interfaces`. If an engine also has `"realtime"`, show a small "Also serves
   realtime" indicator badge on the engine card.
2. **Unified Engines** — New section that lists engines whose `interfaces`
   include BOTH `"batch"` AND `"realtime"`. Each card shows combined metrics:
   batch queue depth + realtime active sessions / capacity. Links to a unified
   detail page (see 2C).
3. **Realtime-Only Workers** — Kept for engines with `interfaces: ["realtime"]`
   only. Same cards as today but with richer status.

**Logic change:**
- Deduplicate: when building `batchEngines` and `realtimeWorkers`, also build a
  `unifiedEngines` list by matching `engine_id` across both arrays (or checking
  `interfaces`).
- Summary cards update: "Batch Engines: X/Y healthy" → include unified count.
  Add a "Unified" count badge.

### 2B. Show models for ALL stages (not just transcribe)

**File:** `web/src/pages/Engines.tsx` (stage accordion engine cards)
**File:** `web/src/pages/EngineDetail.tsx` (detail page)

- Remove the `stage === 'transcribe'` guard from model badge rendering in
  `Engines.tsx` (~line 194) and from the capabilities/models sections in
  `EngineDetail.tsx` (~lines 286, 318).
- Query `useModelRegistry({ engine_id })` for any engine, regardless of stage.
- The backend already supports `GET /v1/models?engine_id=pyannote` — no
  backend change needed.

### 2C. Unified engine detail page

**File:** `web/src/pages/EngineDetail.tsx`

- Detect whether the engine has dual interfaces (from the `interfaces` field
  in `BatchEngine` or by also searching `realtime_engines`).
- If unified: render both batch metrics (queue depth, processing) AND realtime
  metrics (active sessions, capacity, utilization bar) on the same page.
- Show a "Supported Interfaces" section with badges: `Batch`, `Realtime`.
- Merge the capabilities display from both current detail pages:
  - Batch: queue depth, processing count
  - Realtime: session utilization, loaded models, vocabulary support
  - Shared: model registry, hardware info

---

## Phase 3: Capabilities — Move from engine to model

### 3A. Engine detail capabilities → per-model display

**File:** `web/src/pages/EngineDetail.tsx`

- Remove the engine-level "Capabilities" card that shows `supports_word_timestamps`
  and `supports_native_streaming` as engine-wide booleans.
- Instead, show capabilities **per model** in the Available Models grid:
  - Each model card already shows badges; make them more prominent.
  - Add a summary line: "3/5 models support word timestamps".

### 3B. Dashboard CapabilitiesCard — show model counts

**File:** `web/src/components/CapabilitiesCard.tsx`

- Keep the 4 capability indicators but change from boolean checkmarks to
  counts: "Word Timestamps: 8 models" / "Streaming: 3 models".
- The backend `GET /v1/engines/capabilities` already returns aggregate data;
  extend it with model counts or compute client-side from the model registry.

---

## Phase 4: Model filtering fixes

### 4A. Dynamic runtime filter

**File:** `web/src/components/ModelFiltersBar.tsx`

- Replace the hardcoded `RUNTIMES` array with a dynamic list.
- Fetch distinct `engine_id` values from the model registry response
  (already available client-side) or from the engines list.
- Sort alphabetically; no hardcoding.

### 4B. Rename "Runtime" → "Engine"

**Files:**
- `web/src/components/ModelTable.tsx` — column header
- `web/src/components/ModelFiltersBar.tsx` — filter label
- `web/src/lib/strings.ts` — if there's a string constant

- Change "Runtime" to "Engine" everywhere in the Models page to match the
  rest of the console.

### 4C. NewJob — filter models by batch-capable engines

**File:** `web/src/pages/NewJob.tsx`

- Fetch engines via `useEngines()` and build a set of engine_ids that have
  `"batch"` in their `interfaces`.
- Filter the model list: only show models whose `engine_id` is in that set.
- This prevents selecting a model that no batch engine can process.

### 4D. RealtimeLive — use `interfaces` instead of list membership

**File:** `web/src/pages/RealtimeLive.tsx`

- Currently builds `rtRuntimes` from `realtime_engines[].engine_id`.
- With `interfaces` field available, this still works but could be more
  explicit: filter engines where `interfaces.includes("realtime")`.
- Minor change, mostly for correctness documentation.

---

## Phase 5: Dashboard unification

### 5A. Add unified engine utilization to Dashboard

**File:** `web/src/pages/Dashboard.tsx`

- Add an "Engine Utilization" card that shows:
  - Total engines (batch + realtime + unified)
  - Utilization: active_batch + active_realtime across all engines
  - For unified engines, show how capacity is split

### 5B. Merge recent activity or add cross-reference

- Keep the two-column layout (recent jobs / recent sessions) since they ARE
  different resource types.
- Add a subtle link/badge on each item showing which engine processed it.

---

## Phase 6: Pipeline stages

### 6A. Fetch stages dynamically

**File:** `web/src/pages/Engines.tsx`

- Replace the hardcoded `PIPELINE_STAGES` array.
- Derive stages from the actual engine data: `[...new Set(batchEngines.map(e => e.stage))]`.
- Keep the ordered list as a preference hint for sorting, but show any stage
  that exists in the data even if not in the hint list.
- On each stage that has a unified engine, show a small realtime indicator.

---

## Change Summary by File

| File | Changes |
|------|---------|
| `dalston/gateway/api/console.py` | Add `interfaces` to BatchEngine + RealtimeWorker; widen status |
| `web/src/api/types.ts` | Add `interfaces` field; widen WorkerStatus.status |
| `web/src/pages/Engines.tsx` | Unified section; dynamic stages; models for all stages |
| `web/src/pages/EngineDetail.tsx` | Dual-interface support; capabilities per-model; models for all stages |
| `web/src/pages/RealtimeWorkerDetail.tsx` | Richer status display; link to unified view if applicable |
| `web/src/components/CapabilitiesCard.tsx` | Model counts instead of booleans |
| `web/src/components/ModelFiltersBar.tsx` | Dynamic runtimes; rename "Runtime" → "Engine" |
| `web/src/components/ModelTable.tsx` | Rename "Runtime" → "Engine" column header |
| `web/src/pages/NewJob.tsx` | Filter models by batch-capable engine_ids |
| `web/src/pages/RealtimeLive.tsx` | Minor: use interfaces field |
| `web/src/pages/Dashboard.tsx` | Engine utilization card |

---

## Implementation Order

1. **Phase 1** (Backend) — Must go first; frontend depends on `interfaces` field.
2. **Phase 4B** (Terminology) — Quick win, no dependencies.
3. **Phase 4A** (Dynamic filters) — Quick win, no dependencies.
4. **Phase 2B** (Models for all stages) — Quick win, remove hardcoded guard.
5. **Phase 3** (Capabilities per-model) — Moderate, UI restructure.
6. **Phase 1C + 2A** (Frontend types + Engines page restructure) — Core change.
7. **Phase 2C** (Unified detail page) — Builds on 2A.
8. **Phase 4C** (NewJob filter) — Builds on interfaces field.
9. **Phase 5** (Dashboard) — Independent, can be done anytime after Phase 1.
10. **Phase 6** (Dynamic stages) — Independent, can be done anytime.
