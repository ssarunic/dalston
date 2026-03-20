# 13 — Model Registry

**Route:** `/models`
**Component:** `src/pages/Models.tsx`
**Auth required:** Yes

## Purpose

Central registry of all ML models known to the system. Allows operators to browse, filter, pull (download), remove, and purge models, as well as import new models from HuggingFace.

## Storyboard

```
┌──────────────────────────────────────────────────────────────┐
│                                                              │
│  Models                          [🔄 Sync with Disk] [+ Add]│
│  Manage registered models                                    │
│                                                              │
│  ┌────────────────────────────────────────────────────────┐  │
│  │ Model Registry                                         │  │
│  │                                                        │  │
│  │  [🔍 Search...] [Status ▾] [Stage ▾] [Engine ID ▾]    │  │
│  │                                                        │  │
│  │  ┌──────────────────────────────────────────────────┐  │  │
│  │  │ ID           │ Name      │ Engine  │ Status │ Act│  │  │
│  │  │──────────────┼───────────┼─────────┼────────┼────│  │  │
│  │  │ whisper-lg-v3│ Whisper   │ faster- │ 🟢 Rdy │ ···│  │  │
│  │  │ whisper-base │ Whisper B │ faster- │ 🟢 Rdy │ ···│  │  │
│  │  │ parakeet-0.6 │ Parakeet  │ parakeet│ ⬇ Pull │ ···│  │  │
│  │  │ whisper-tiny │ Whisper T │ faster- │ ⚪ Not  │ ···│  │  │
│  │  └──────────────────────────────────────────────────┘  │  │
│  └────────────────────────────────────────────────────────┘  │
│                                                              │
│  ┌────────────────────────────────────────────────────────┐  │
│  │ Add Model from HuggingFace              [×]            │  │
│  │                                                        │  │
│  │  Model ID: [Systran/faster-whisper-large-v3    ]       │  │
│  │                                                        │  │
│  │  [Resolve & Register]                                  │  │
│  │                                                        │  │
│  │  ✅ Model resolved: Whisper Large V3                   │  │
│  │     Size: 2.9 GB · Supports: word timestamps           │  │
│  └────────────────────────────────────────────────────────┘  │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

## Layout

**Header:** Title + subtitle on the left; "Sync with Disk" button and "Add from HuggingFace" button on the right.

**Body:** Single card containing the filter bar and model table.

## Elements

### Header Actions

| Button | Icon | Description |
|--------|------|-------------|
| Sync with Disk | RefreshCw (spins while syncing) | Triggers `useSyncModels()` — re-scans disk for model changes |
| Add from HuggingFace | Plus | Opens AddModelDialog for importing HF models |

### Filter Bar (`<ModelFiltersBar>`)

| Filter | Type | Description |
|--------|------|-------------|
| Search | Text input | Client-side filter on model ID, name, or engine ID |
| Status | Select | Filter by model status (ready, not downloaded, etc.) |
| Stage | Select | Filter by pipeline stage |
| Engine ID | Select | Populated dynamically from all known engine IDs |

### Model Table (`<ModelTable>`)

Displays filtered models with columns for ID, name, engine, status, and actions.

| Action | Description |
|--------|-------------|
| Pull | Download a model that isn't yet on disk. Uses `force=true` for failed models to retry. |
| Remove | Unregister a model from the registry |
| Purge | Remove model and delete downloaded files |

Action buttons show loading spinners with the specific model ID being acted upon.

### Add Model Dialog (`<AddModelDialog>`)

| Element | Description |
|---------|-------------|
| Model ID input | HuggingFace model identifier (e.g. `Systran/faster-whisper-large-v3`) |
| Resolve button | Calls `useResolveHFModel` with `auto_register: true` |
| Result display | Shows resolved model info or error |

## Behaviour

- Data from `useModelRegistry(filters)` — fetches from model registry API.
- A second unfiltered `useModelRegistry()` call derives available engine IDs for the filter dropdown.
- Search filtering is client-side (filters on ID, name, engine ID).
- Loading state: centered spinner + "Loading models" text.
- Empty state: Package icon + "No models found" + hint text.
- Error state: centered AlertCircle + red error message.
- No pagination — all models loaded at once.
- No responsive mobile card layout — table only.
