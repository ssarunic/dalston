# Console Model Management - Implementation Spec

This document provides detailed specifications for the M42 web console model management features.

## Component Specifications

### 1. Model Status Visual Design

#### Status Indicators

```text
Status          Color           Icon            Animation
──────────────────────────────────────────────────────────
ready           green-500       CheckCircle     none
downloading     yellow-500      Loader2         spin
not_downloaded  gray-400        Cloud           none
failed          red-500         AlertCircle     none
```

#### Status Badge Component

```tsx
const statusConfig = {
  ready: {
    label: 'Ready',
    className: 'bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-400',
  },
  downloading: {
    label: 'Downloading',
    className: 'bg-yellow-100 text-yellow-800 dark:bg-yellow-900/30 dark:text-yellow-400',
  },
  not_downloaded: {
    label: 'Not Downloaded',
    className: 'bg-gray-100 text-gray-800 dark:bg-gray-800 dark:text-gray-400',
  },
  failed: {
    label: 'Failed',
    className: 'bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-400',
  },
};
```

### 2. ModelCard Layout Specifications

```text
┌─────────────────────────────────────────────────────────────────┐
│  parakeet-tdt-1.1b                              ● Ready  1.1 GB │  <- Header
│  NeMo Parakeet TDT 1.1B - English ASR                           │  <- Subtitle
├─────────────────────────────────────────────────────────────────┤
│  [nemo]  [transcribe]                                           │  <- Runtime/Stage badges
│                                                                  │
│  [timestamps] [punctuation] [CPU]                               │  <- Capability badges
│                                                                  │
│  🌐 en                                                          │  <- Languages
│  ⬇️ 45,231  ❤️ 892                                              │  <- HF stats
│                                                                  │
│  VRAM: 4GB • RAM: 8GB                                           │  <- Hardware (if present)
├─────────────────────────────────────────────────────────────────┤
│  [View on HF]                                    [Remove]       │  <- Actions
└─────────────────────────────────────────────────────────────────┘

Card Width: 320px min, flexible
Card Height: Auto (content-driven)
Padding: 16px (p-4)
Gap between cards: 16px
```

#### Downloading State

```text
┌─────────────────────────────────────────────────────────────────┐
│  whisper-large-v3                          ● Downloading  3.1GB │
│  OpenAI Whisper Large V3                                        │
├─────────────────────────────────────────────────────────────────┤
│  [faster-whisper]  [transcribe]                                 │
│                                                                  │
│  ████████████░░░░░░░░░░░░░░░░░░░░░░░░░░  45%                   │  <- Progress bar
│                                                                  │
│  Downloading... 1.4 GB / 3.1 GB                                 │  <- Progress text
└─────────────────────────────────────────────────────────────────┘
```

#### Failed State

```text
┌─────────────────────────────────────────────────────────────────┐
│  distil-whisper-large-v3                      ● Failed   1.5 GB │
│  Distilled Whisper Large V3                                     │
├─────────────────────────────────────────────────────────────────┤
│  [faster-whisper]  [transcribe]                                 │
│                                                                  │
│  ⚠️ Error: Network timeout during download                      │  <- Error message
├─────────────────────────────────────────────────────────────────┤
│  [View on HF]                                     [Retry]       │  <- Retry action
└─────────────────────────────────────────────────────────────────┘
```

### 3. ModelSelector Component Specifications

#### Collapsed State

```text
┌─────────────────────────────────────────────────────────────────┐
│  Model                                                          │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │  🔮 Auto (Recommended)                                  ▼ │  │
│  └───────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘

Or with selected model:

┌─────────────────────────────────────────────────────────────────┐
│  Model                                                          │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │  [nemo] parakeet-tdt-1.1b                               ▼ │  │
│  └───────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

#### Expanded State (Popover)

```text
┌─────────────────────────────────────────────────────────────────┐
│  ┌───────────────────────────────────────────────────────────┐  │
│  │ 🔍 Search models...                                       │  │
│  └───────────────────────────────────────────────────────────┘  │
│                                                                  │
│  ── Recommended ──────────────────────────────────────────────  │
│  │ 🔮 Auto                                                   │  │
│  │    Best model for your settings                           │  │
│                                                                  │
│  ── faster-whisper ───────────────────────────────────────────  │
│  │ whisper-large-v3                              3.1 GB     │  │
│  │    99 languages • timestamps                              │  │
│  │──────────────────────────────────────────────────────────│  │
│  │ whisper-large-v3-turbo                        1.5 GB     │  │
│  │    99 languages • timestamps                              │  │
│  │──────────────────────────────────────────────────────────│  │
│  │ whisper-medium                                1.5 GB     │  │
│  │    99 languages • timestamps                              │  │
│                                                                  │
│  ── nemo ─────────────────────────────────────────────────────  │
│  │ parakeet-tdt-1.1b                             1.1 GB  ✓  │  │
│  │    en • timestamps                                        │  │
│  │──────────────────────────────────────────────────────────│  │
│  │ parakeet-ctc-0.6b                             0.6 GB     │  │
│  │    en                                                     │  │
│                                                                  │
│  ── Custom HuggingFace ───────────────────────────────────────  │
│  │ ┌─────────────────────────────────────────────┐ [Use]   │  │
│  │ │ nvidia/canary-1b                            │          │  │
│  │ └─────────────────────────────────────────────┘          │  │
│  │ e.g., nvidia/parakeet-tdt-1.1b                           │  │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘

Popover Width: 400px
Max Height: 400px (with scroll)
```

### 4. HFModelInput Resolution Flow

```text
User enters: "nvidia/canary-1b"
                    │
                    ▼
┌─────────────────────────────────────────────────────────────────┐
│  ➕ Add model from HuggingFace                                   │
│  ┌─────────────────────────────────────────────────────────────┐│
│  │ nvidia/canary-1b                              [Resolving...] ││
│  └─────────────────────────────────────────────────────────────┘│
│                                                                  │
│  ⏳ Resolving model...                                           │
└─────────────────────────────────────────────────────────────────┘
                    │
                    ▼ (Success)
┌─────────────────────────────────────────────────────────────────┐
│  ➕ Add model from HuggingFace                                   │
│  ┌─────────────────────────────────────────────────────────────┐│
│  │ nvidia/canary-1b                                            ││
│  └─────────────────────────────────────────────────────────────┘│
│                                                                  │
│  ┌─────────────────────────────────────────────────────────────┐│
│  │ ✓ Resolved to runtime: nemo                                 ││
│  │   Library: nemo                                             ││
│  │   Languages: en, es, de, fr                                 ││
│  │   45,231 downloads • 892 likes                              ││
│  │                                                  [Add Model] ││
│  └─────────────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────────┘
                    │
                    ▼ (Cannot resolve)
┌─────────────────────────────────────────────────────────────────┐
│  ➕ Add model from HuggingFace                                   │
│  ┌─────────────────────────────────────────────────────────────┐│
│  │ random/unknown-model                                        ││
│  └─────────────────────────────────────────────────────────────┘│
│                                                                  │
│  ┌─────────────────────────────────────────────────────────────┐│
│  │ ⚠️ Could not determine runtime for this model.              ││
│  │   It may not be a supported ASR model, or the library_name  ││
│  │   is not recognized.                                        ││
│  │                                                             ││
│  │   Supported libraries: ctranslate2, nemo, transformers      ││
│  └─────────────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────────┘
```

### 5. Download Progress Indicator

#### Header Position

```text
┌─────────────────────────────────────────────────────────────────┐
│  ≡ DALSTON                          [⬇️ 2]  [🔔]  [👤]         │
│                                       ↑                         │
│                                  Download indicator              │
└─────────────────────────────────────────────────────────────────┘
```

#### Expanded Popover

```text
                                    ┌─────────────────────────────┐
                                    │ Downloading Models          │
                                    │ ─────────────────────────── │
                                    │                             │
                                    │ whisper-large-v3       45%  │
                                    │ ████████░░░░░░░░░░░░░░░░░░ │
                                    │                             │
                                    │ parakeet-tdt-1.1b      78%  │
                                    │ ████████████████░░░░░░░░░░ │
                                    │                             │
                                    │ [View All →]               │
                                    └─────────────────────────────┘
```

### 6. Capabilities Card Layout

```text
┌─────────────────────────────────────────────────────────────────┐
│  ⚡ System Capabilities                                         │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  Languages                                                       │
│  🌐 99 supported (en, es, fr, de, zh...)                        │
│                                                                  │
│  Features                                                        │
│  ┌──────────────────────┬──────────────────────┐                │
│  │ ✅ Word Timestamps   │ ✅ Speaker Diarize   │                │
│  ├──────────────────────┼──────────────────────┤                │
│  │ ✅ PII Detection     │ ✅ Real-time Stream  │                │
│  └──────────────────────┴──────────────────────┘                │
│                                                                  │
│  Models Ready: 5 / 12                                           │
│                                                                  │
│  [View All Models →]                                            │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

### 7. Engine Card with Model Info

```text
┌─────────────────────────────────────────────────────────────────┐
│  faster-whisper                                    ● Running    │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  Queue: 3  │  Processing: 1                                     │
│                                                                  │
│  Loaded: [whisper-large-v3]                                     │
│                                                                  │
│  Available:                                                      │
│  [whisper-large-v3] [whisper-medium] [whisper-large-v3-turbo]  │
│  [+2 more]                                                      │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

---

## Data Flow Diagrams

### Model Pull Flow

```text
┌──────────┐      ┌──────────┐      ┌──────────┐      ┌──────────┐
│  User    │      │  UI      │      │  API     │      │ Backend  │
│  clicks  │─────▶│ calls    │─────▶│ POST     │─────▶│ starts   │
│  "Pull"  │      │ pullModel│      │ /pull    │      │ download │
└──────────┘      └──────────┘      └──────────┘      └──────────┘
                        │                                   │
                        │   invalidateQueries              │
                        │◀──────────────────────────────────
                        │                                   │
                        ▼                                   │
                  ┌──────────┐                              │
                  │ Status   │                              │
                  │ updates  │◀─────────(polling)───────────
                  │ to       │
                  │"download │
                  │  ing"    │
                  └──────────┘
                        │
                        ▼ (eventually)
                  ┌──────────┐
                  │ Status   │
                  │ updates  │
                  │ to       │
                  │ "ready"  │
                  └──────────┘
                        │
                        ▼
                  ┌──────────┐
                  │ Toast    │
                  │ notifies │
                  │ user     │
                  └──────────┘
```

### HuggingFace Resolution Flow

```text
┌──────────┐      ┌──────────┐      ┌──────────┐      ┌──────────┐
│  User    │      │  UI      │      │  API     │      │ HF Hub   │
│  enters  │─────▶│ calls    │─────▶│ POST     │─────▶│ fetches  │
│  HF ID   │      │ resolve  │      │ /resolve │      │ model    │
└──────────┘      └──────────┘      └──────────┘      │ info     │
                        │                             └──────────┘
                        │                                   │
                        │   ┌────────────────────────────────
                        │   │ {
                        │   │   model_id: "nvidia/canary",
                        │   │   resolved_runtime: "nemo",
                        │   │   library_name: "nemo",
                        │   │   languages: ["en", "es"],
                        │   │   downloads: 45231,
                        │   │   likes: 892
                        │   │ }
                        │◀──┘
                        │
                        ▼
                  ┌──────────┐
                  │ Display  │
                  │ result   │
                  │ + option │
                  │ to add   │
                  └──────────┘
                        │
                        │ (if auto_register: true)
                        ▼
                  ┌──────────┐
                  │ Model    │
                  │ added to │
                  │ registry │
                  │ with     │
                  │ "not_dl" │
                  │ status   │
                  └──────────┘
```

---

## Query Key Structure

Consistent React Query key patterns:

```typescript
// Model registry
['modelRegistry']                           // List all
['modelRegistry', { stage, runtime, status }] // List with filters
['modelRegistry', modelId]                  // Single model

// Catalog models (existing)
['models']                                  // List all
['models', { stage }]                       // List with filters
['models', modelId]                         // Single model

// Engines
['engines']                                 // List all engines
['engines', engineId]                       // Single engine

// Capabilities
['capabilities']                            // System capabilities

// HF resolution (mutation, no cache)
// Uses useMutation, not useQuery
```

---

## Error States

### Network Error

```tsx
<Alert variant="destructive">
  <AlertCircle className="h-4 w-4" />
  <AlertTitle>Failed to load models</AlertTitle>
  <AlertDescription>
    Could not connect to the server. Please check your connection and try again.
    <Button variant="link" onClick={refetch}>Retry</Button>
  </AlertDescription>
</Alert>
```

### Empty States

```tsx
// No models at all
<div className="text-center py-12">
  <Box className="h-12 w-12 mx-auto text-muted-foreground" />
  <h3 className="mt-4 text-lg font-medium">No models registered</h3>
  <p className="mt-2 text-muted-foreground">
    Add your first model from HuggingFace to get started.
  </p>
</div>

// No search results
<div className="text-center py-8">
  <SearchX className="h-8 w-8 mx-auto text-muted-foreground" />
  <p className="mt-2 text-muted-foreground">
    No models match your search. Try different keywords or clear filters.
  </p>
</div>

// No ready models for job creation
<div className="text-sm text-muted-foreground">
  No models are ready. Visit the <Link to="/models">Models page</Link> to download one.
</div>
```

---

## Accessibility Requirements

1. **Keyboard Navigation**
   - Tab through model cards
   - Enter to expand/collapse
   - Arrow keys in model selector popover

2. **ARIA Labels**

   ```tsx
   <Button aria-label={`Download ${model.id}`}>Pull</Button>
   <Button aria-label={`Remove ${model.id}`}>Remove</Button>
   <div role="status" aria-live="polite">{downloads.length} models downloading</div>
   ```

3. **Focus Management**
   - Focus trapped in model selector popover
   - Focus returned to trigger after close
   - Focus indicator visible on all interactive elements

4. **Screen Reader Support**
   - Status announced when download starts/completes
   - Progress percentage announced periodically
   - Error messages announced immediately

---

## Performance Considerations

1. **Model List**
   - Virtualize if > 50 models
   - Debounce search input (300ms)
   - Skeleton loading during fetch

2. **Download Polling**
   - Poll every 5s while any download active
   - Stop polling when no downloads
   - Use visibility API to pause when tab hidden

3. **Images/Icons**
   - Use Lucide icons (tree-shakable)
   - Lazy load HuggingFace card images if added

4. **Caching**
   - 30s staleTime for model registry
   - 60s staleTime for capabilities
   - Invalidate on mutations

---

## Testing Checklist

### Model Registry Page

- [ ] Models load and display correctly
- [ ] Status grouping works (Ready, Downloading, Available, Failed)
- [ ] Search filters models by ID and name
- [ ] Stage filter works
- [ ] Runtime filter works
- [ ] Status filter works
- [ ] Clear filters resets all
- [ ] Pull button starts download
- [ ] Remove button deletes model
- [ ] Sync button refreshes from disk
- [ ] HF input resolves model
- [ ] HF input shows error for unknown model

### Model Selector

- [ ] Opens popover on click
- [ ] Search filters options
- [ ] Auto option selectable
- [ ] Groups show by runtime
- [ ] Custom HF input works
- [ ] Selected model shows checkmark
- [ ] Closes on selection
- [ ] Closes on escape key

### Download Progress

- [ ] Indicator shows count
- [ ] Popover shows all downloads
- [ ] Progress updates
- [ ] Toast on completion
- [ ] Toast on failure

### Capabilities Card

- [ ] Shows language count
- [ ] Shows feature availability
- [ ] Shows model counts
- [ ] Link navigates to Models page

### Responsive

- [ ] Model cards reflow on mobile
- [ ] Model selector usable on mobile
- [ ] Filters stack on mobile
