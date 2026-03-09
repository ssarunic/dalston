# M42: Console Model Management

| | |
|---|---|
| **Goal** | Web UI for model discovery, download management, and enhanced job creation with model selection |
| **Duration** | 5-7 days |
| **Dependencies** | M40 (Model Registry & Aliases), M10 (Web Console) |
| **Deliverable** | Model Registry page, enhanced NewJob model selector, engine model visibility, download progress UI |
| **Status** | Complete |

## User Story

> *"As an admin, I can browse available models, download them from HuggingFace, and select the right model when creating transcription jobs — all from the web console."*

---

## Overview

M39/M40 added powerful model management capabilities (database registry, HF auto-detection, CLI commands), but these are only accessible via CLI. This milestone exposes those capabilities in the web console.

### Key Features

1. **Model Registry Page**: Browse, search, filter all models with download management
2. **Enhanced Job Creation**: Searchable model selector with capabilities display
3. **Engine Model Visibility**: See loaded/available models per engine
4. **Download Progress**: Global notification for background model downloads
5. **System Capabilities**: Dashboard card showing aggregate capabilities

---

## Phases

| Phase | Focus | Days | Priority |
|-------|-------|------|----------|
| 42.1 | Model Registry Page | 2-3 | P0 |
| 42.2 | Enhanced NewJob Model Selector | 1-2 | P0 |
| 42.3 | Engine Model Visibility | 1 | P1 |
| 42.4 | Download Progress UI | 0.5 | P1 |
| 42.5 | Dashboard Capabilities Card | 0.5 | P2 |

---

## Phase 42.1: Model Registry Page

**Goal:** Dedicated page for browsing and managing models.

### Route Setup

Added `/models` and `/models/:modelId` routes and a Models nav item in the sidebar.

*Implementation: see `web/src/App.tsx` and `web/src/components/Sidebar.tsx`*

### API Types

Defined `ModelRegistryEntry`, `HFResolveRequest`, `HFResolveResponse`, and `ModelFilters` types covering model status, capabilities, hardware requirements, and HuggingFace metadata.

*Implementation: see `web/src/api/types.ts`*

### API Client Methods

Added client methods for model registry CRUD: `getModelRegistry`, `getModelRegistryEntry`, `pullModel`, `removeModel`, `resolveHFModel`, `getHFMappings`, and `syncModels`.

*Implementation: see `web/src/api/client.ts`*

### React Query Hooks

Created hooks for model registry queries and mutations: `useModelRegistry`, `useModelRegistryEntry`, `usePullModel`, `useRemoveModel`, `useResolveHFModel`, and `useSyncModels`, all with query invalidation on mutation success.

*Implementation: see `web/src/hooks/useModelRegistry.ts`*

### Models Page Component

Models page displays models grouped by status (downloading, ready, available, failed) in a card grid. Includes a "Sync with Disk" button and an "Add from HuggingFace" section. Each status group shows appropriate actions (pull, remove, retry).

*Implementation: see `web/src/pages/Models.tsx`*

### ModelCard Component

Card component displaying model metadata: status indicator, runtime/stage badges, capability badges (timestamps, punctuation, streaming, CPU), languages, HuggingFace stats (downloads, likes), hardware requirements, download progress bar, and action buttons (Pull, Remove, View on HF).

*Implementation: see `web/src/components/ModelCard.tsx`*

### ModelFiltersBar Component

Filter bar with search input, stage dropdown, runtime dropdown, and status dropdown, plus a "Clear filters" button.

*Implementation: see `web/src/components/ModelFiltersBar.tsx`*

### HFModelInput Component

Form for entering a HuggingFace model ID (e.g., `nvidia/parakeet-tdt-1.1b`), resolving it to a runtime, and displaying resolution results including library, languages, and download/like counts. Implemented as AddModelDialog.

*Implementation: see `web/src/components/HFModelInput.tsx`*

### Deliverables

- [x] Route `/models` added to App.tsx
- [x] Models nav item in Sidebar
- [x] `useModelRegistry` hook with CRUD operations
- [x] `Models` page with filtering and grouping by status
- [x] `ModelCard` component showing all model metadata
- [x] `ModelFiltersBar` component with search and dropdowns
- [x] `HFModelInput` component for adding HF models (implemented as AddModelDialog)
- [x] Pull/Remove actions working
- [x] Sync with disk button

---

## Phase 42.2: Enhanced NewJob Model Selector

**Goal:** Replace simple dropdown with searchable, informative model selector.

### ModelSelector Component

Searchable popover-based model selector that groups ready models by runtime, supports an "Auto (Recommended)" option, filters by language compatibility, and links to the Models page for registering more models. Uses Command/Combobox pattern with keyboard navigation.

*Implementation: see `web/src/components/ModelSelector.tsx`*

### Update NewJob.tsx

Replaced the existing model Select dropdown with ModelSelector, passing the selected language for filtering. Shows a `ModelCompatibilityWarning` when a specific (non-auto) model is selected.

*Implementation: see `web/src/pages/NewJob.tsx`*

### ModelCompatibilityWarning Component

Displays warnings when a selected model is not downloaded, currently downloading, failed to download, or does not support the selected language.

*Implementation: see `web/src/components/ModelCompatibilityWarning.tsx`*

### Deliverables

- [x] `ModelSelector` component with search and grouping (enhanced with keyboard navigation + autocomplete)
- [x] Auto option with explanation
- [x] **Orchestrator Auto model selection**: When "Auto" is selected, orchestrator queries registry for downloaded models and picks the best one based on language compatibility and model size (instead of hardcoded fallback)
- [x] Custom HuggingFace model input (via link to Models page)
- [x] Language-aware filtering
- [x] `ModelCompatibilityWarning` component
- [x] Integration in NewJob.tsx
- [x] Show model capabilities inline (languages, timestamps, size)

---

## Phase 42.3: Engine Model Visibility

**Goal:** Show loaded and available models per engine on the Engines page.

Enhanced engine cards to display the currently loaded model as a badge and a collapsible list of available models (with "+N more" overflow). The `/v1/engines` endpoint already returns `loaded_model` and `available_models` fields from M40.

*Implementation: see `web/src/pages/Engines.tsx`*

### Deliverables

- [x] Engine cards show currently loaded model
- [x] Engine cards show available models (collapsed view with +N more)
- [x] Click to expand full model list (badge overflow)
- [ ] Link to model detail from engine card

---

## Phase 42.4: Download Progress UI

**Goal:** Global notification system for model downloads.

### Download Progress Context

Context provider that polls the model registry for models with `downloading` status and exposes active download state to the component tree.

*Implementation: see `web/src/contexts/DownloadContext.tsx`*

### DownloadIndicator Component

Header button with badge count that opens a popover showing active downloads with progress bars. Integrated into the layout header.

*Implementation: see `web/src/components/DownloadIndicator.tsx` and `web/src/components/Layout.tsx`*

### Toast Notifications

Polls for model status transitions and shows toast notifications when downloads complete or fail.

*Implementation: see `web/src/hooks/useModelRegistry.ts`*

### Deliverables

- [x] `DownloadIndicator` floating component (like `LiveSessionIndicator`)
- [x] Shows active download count and overall progress
- [x] Toast notifications for download completion/failure
- [x] Auto-refresh polling (3s active, 30s idle)
- [x] Click to navigate to Models page

---

## Phase 42.5: Dashboard Capabilities Card

**Goal:** Show system-wide capabilities on the dashboard.

### Capabilities Hook

Query hook fetching `/v1/capabilities` for system-wide language support, feature availability (timestamps, diarization, PII detection, streaming), engine counts by stage, and model readiness.

*Implementation: see `web/src/hooks/useCapabilities.ts`*

### CapabilitiesCard Component

Dashboard card displaying supported language count, feature availability indicators (checkmark/X for each feature), models ready count, and a link to the Models page. Added to the dashboard stats grid.

*Implementation: see `web/src/components/CapabilitiesCard.tsx` and `web/src/pages/Dashboard.tsx`*

### Deliverables

- [x] `useSystemCapabilities` hook
- [x] `CapabilitiesCard` component
- [x] Languages count with preview
- [x] Feature availability indicators
- [x] Models ready count
- [x] Link to Models page

---

## API Endpoint Summary

### Existing Endpoints (from M40)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/v1/models` | GET | List catalog models |
| `/v1/models/{id}` | GET | Get catalog model |
| `/v1/models/{id}/pull` | POST | Start model download |
| `/v1/models/{id}` | DELETE | Remove downloaded model |
| `/v1/models/sync` | POST | Sync registry with disk |
| `/v1/models/registry` | GET | List registry entries with status |
| `/v1/models/registry/{id}` | GET | Get registry entry |
| `/v1/models/hf/resolve` | POST | Resolve HF model to runtime |
| `/v1/models/hf/mappings` | GET | Get library/tag mappings |
| `/v1/capabilities` | GET | System-wide capabilities |

### No New Backend Endpoints Required

All necessary endpoints exist from M40. This milestone is frontend-only.

---

## Verification

- [ ] Models page loads at `/models` with correct statuses, search, and filters working
- [ ] HuggingFace model resolution works (e.g., `nvidia/parakeet-tdt-1.1b` resolves to `nemo` runtime)
- [ ] Enhanced model selector on `/jobs/new` shows grouped models with search, auto option, and compatibility warnings
- [ ] Engine cards on `/engines` show loaded and available models
- [ ] Download indicator appears in header during model pull, with toast on completion

---

## Checkpoint

- [x] **42.1**: Model Registry page with filtering and actions
- [x] **42.2**: Enhanced NewJob model selector with search (keyboard nav + autocomplete)
  - [x] Orchestrator Auto model selection: queries registry for best downloaded model
  - [x] Graceful error handling: `NoDownloadedModelError` when no models available
- [x] **42.3**: Engine cards show model information
- [x] **42.4**: Download progress indicator and notifications
- [x] **42.5**: Dashboard capabilities card

---

## Risk Mitigation

| Risk | Mitigation |
|------|------------|
| Large model list performance | Virtual scrolling if > 100 models |
| Download progress not updating | Poll every 5s while downloads active |
| HF resolution timeout | 10s timeout with clear error message |
| Model selector popover too tall | Max height with scroll |
| Custom HF model fails silently | Show resolution result before using |
| Auto model selection fails with missing model | Orchestrator queries registry for downloaded models instead of hardcoded fallback; raises `NoDownloadedModelError` with clear message if none available |

---

## Future Considerations

Not in scope for M42:

- **Model comparison**: Side-by-side capability comparison
- **Model benchmarks**: Show RTF, accuracy metrics
- **Batch model download**: Download multiple models at once
- **Model presets**: Save favorite model configurations
- **Usage analytics**: Track which models are most used

**Next**: M43 (to be determined)
