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

```text
┌─────────────────────────────────────────────────────────────────────────────┐
│                         MODEL REGISTRY PAGE                                  │
├─────────────────────────────────────────────────────────────────────────────┤
│  [🔍 Search models...]  [Stage: All ▼]  [Runtime: All ▼]  [Status: All ▼]  │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │ parakeet-tdt-1.1b                                    ● Ready  1.1GB │   │
│  │ NeMo Parakeet TDT 1.1B - English ASR with timestamps               │   │
│  │ 🏷️ nemo  │  🌐 en  │  ⏱️ word_timestamps  │  ⬇️ 45K  │  ❤️ 892     │   │
│  │                                              [Remove] [View on HF]  │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                                                              │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │ whisper-large-v3                                     ○ Not DL  3.1GB│   │
│  │ OpenAI Whisper Large V3 - Multilingual ASR                         │   │
│  │ 🏷️ faster-whisper  │  🌐 99 langs  │  ⏱️ word_timestamps           │   │
│  │                                                             [Pull]  │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                                                              │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │ ➕ Add model from HuggingFace                                        │   │
│  │ ┌─────────────────────────────────────────────────────────────────┐ │   │
│  │ │ nvidia/canary-1b                                                │ │   │
│  │ └─────────────────────────────────────────────────────────────────┘ │   │
│  │ [Resolve & Add]                                                      │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

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

Add to `web/src/App.tsx`:

```tsx
<Route path="/models" element={<Models />} />
<Route path="/models/:modelId" element={<ModelDetail />} />
```

Update `web/src/components/Sidebar.tsx`:

```tsx
{ to: '/models', icon: Box, label: 'Models' },
```

### API Types

**Add to `web/src/api/types.ts`:**

```typescript
export type ModelStatus = 'not_downloaded' | 'downloading' | 'ready' | 'failed';

export interface ModelRegistryEntry {
  id: string;
  name: string | null;
  runtime: string;
  runtime_model_id: string;
  stage: string;
  status: ModelStatus;
  size_bytes: number | null;
  download_progress?: number;
  downloaded_at: string | null;

  // Capabilities
  word_timestamps: boolean;
  punctuation: boolean;
  streaming: boolean;

  // Hardware
  min_vram_gb: number | null;
  min_ram_gb: number | null;
  supports_cpu: boolean;

  // HuggingFace metadata
  source: string | null;
  library_name: string | null;
  languages: string[] | null;
  metadata: {
    downloads?: number;
    likes?: number;
    tags?: string[];
    pipeline_tag?: string;
  };

  last_used_at: string | null;
  created_at: string;
}

export interface HFResolveRequest {
  model_id: string;
  auto_register?: boolean;
}

export interface HFResolveResponse {
  model_id: string;
  resolved_runtime: string | null;
  library_name: string | null;
  pipeline_tag: string | null;
  languages: string[];
  downloads: number;
  likes: number;
  error?: string;
}

export interface ModelFilters {
  stage?: string;
  runtime?: string;
  status?: ModelStatus;
  search?: string;
}
```

### API Client Methods

**Add to `web/src/api/client.ts`:**

```typescript
// Model Registry
getModelRegistry: async (filters?: ModelFilters): Promise<{ data: ModelRegistryEntry[] }> => {
  const params = new URLSearchParams();
  if (filters?.stage) params.set('stage', filters.stage);
  if (filters?.runtime) params.set('runtime', filters.runtime);
  if (filters?.status) params.set('status', filters.status);
  return client.get(`v1/models/registry?${params}`).json();
},

getModelRegistryEntry: async (modelId: string): Promise<ModelRegistryEntry> => {
  return client.get(`v1/models/registry/${encodeURIComponent(modelId)}`).json();
},

pullModel: async (modelId: string, force?: boolean): Promise<{ message: string; model_id: string }> => {
  return client.post(`v1/models/${encodeURIComponent(modelId)}/pull`, { json: { force } }).json();
},

removeModel: async (modelId: string): Promise<{ message: string }> => {
  return client.delete(`v1/models/${encodeURIComponent(modelId)}`).json();
},

resolveHFModel: async (request: HFResolveRequest): Promise<HFResolveResponse> => {
  return client.post('v1/models/hf/resolve', { json: request }).json();
},

getHFMappings: async (): Promise<{ library_to_runtime: Record<string, string>; tag_to_runtime: Record<string, string> }> => {
  return client.get('v1/models/hf/mappings').json();
},

syncModels: async (): Promise<{ updated: number; unchanged: number }> => {
  return client.post('v1/models/sync').json();
},
```

### React Query Hooks

**Create `web/src/hooks/useModelRegistry.ts`:**

```typescript
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { apiClient } from '../api/client';
import type { ModelFilters, ModelRegistryEntry, HFResolveRequest } from '../api/types';

export function useModelRegistry(filters?: ModelFilters) {
  return useQuery({
    queryKey: ['modelRegistry', filters],
    queryFn: () => apiClient.getModelRegistry(filters),
    staleTime: 30_000,
  });
}

export function useModelRegistryEntry(modelId: string) {
  return useQuery({
    queryKey: ['modelRegistry', modelId],
    queryFn: () => apiClient.getModelRegistryEntry(modelId),
    enabled: !!modelId,
  });
}

export function usePullModel() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ modelId, force }: { modelId: string; force?: boolean }) =>
      apiClient.pullModel(modelId, force),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['modelRegistry'] });
    },
  });
}

export function useRemoveModel() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (modelId: string) => apiClient.removeModel(modelId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['modelRegistry'] });
    },
  });
}

export function useResolveHFModel() {
  return useMutation({
    mutationFn: (request: HFResolveRequest) => apiClient.resolveHFModel(request),
  });
}

export function useSyncModels() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: () => apiClient.syncModels(),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['modelRegistry'] });
    },
  });
}
```

### Models Page Component

**Create `web/src/pages/Models.tsx`:**

```tsx
import { useState } from 'react';
import { useModelRegistry, usePullModel, useRemoveModel, useResolveHFModel, useSyncModels } from '../hooks/useModelRegistry';
import { ModelCard } from '../components/ModelCard';
import { HFModelInput } from '../components/HFModelInput';
import { ModelFiltersBar } from '../components/ModelFiltersBar';
import type { ModelFilters, ModelStatus } from '../api/types';

export default function Models() {
  const [filters, setFilters] = useState<ModelFilters>({});
  const { data, isLoading, error } = useModelRegistry(filters);
  const pullModel = usePullModel();
  const removeModel = useRemoveModel();
  const resolveHF = useResolveHFModel();
  const syncModels = useSyncModels();

  const models = data?.data ?? [];

  // Group models by status for display
  const readyModels = models.filter(m => m.status === 'ready');
  const downloadingModels = models.filter(m => m.status === 'downloading');
  const availableModels = models.filter(m => m.status === 'not_downloaded');
  const failedModels = models.filter(m => m.status === 'failed');

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">Models</h1>
          <p className="text-muted-foreground">
            Manage transcription models and download from HuggingFace
          </p>
        </div>
        <Button
          variant="outline"
          onClick={() => syncModels.mutate()}
          disabled={syncModels.isPending}
        >
          <RefreshCw className={cn("h-4 w-4 mr-2", syncModels.isPending && "animate-spin")} />
          Sync with Disk
        </Button>
      </div>

      {/* Filters */}
      <ModelFiltersBar filters={filters} onChange={setFilters} />

      {/* Add from HuggingFace */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Add Model from HuggingFace</CardTitle>
        </CardHeader>
        <CardContent>
          <HFModelInput
            onResolve={(modelId) => resolveHF.mutate({ model_id: modelId, auto_register: true })}
            isLoading={resolveHF.isPending}
            result={resolveHF.data}
            error={resolveHF.error}
          />
        </CardContent>
      </Card>

      {/* Downloading Models */}
      {downloadingModels.length > 0 && (
        <section>
          <h2 className="text-lg font-semibold mb-3 flex items-center gap-2">
            <Loader2 className="h-4 w-4 animate-spin" />
            Downloading ({downloadingModels.length})
          </h2>
          <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
            {downloadingModels.map(model => (
              <ModelCard key={model.id} model={model} />
            ))}
          </div>
        </section>
      )}

      {/* Ready Models */}
      {readyModels.length > 0 && (
        <section>
          <h2 className="text-lg font-semibold mb-3 flex items-center gap-2">
            <CheckCircle className="h-4 w-4 text-green-500" />
            Ready ({readyModels.length})
          </h2>
          <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
            {readyModels.map(model => (
              <ModelCard
                key={model.id}
                model={model}
                onRemove={() => removeModel.mutate(model.id)}
                isRemoving={removeModel.isPending}
              />
            ))}
          </div>
        </section>
      )}

      {/* Available Models */}
      {availableModels.length > 0 && (
        <section>
          <h2 className="text-lg font-semibold mb-3 flex items-center gap-2">
            <Cloud className="h-4 w-4 text-muted-foreground" />
            Available to Download ({availableModels.length})
          </h2>
          <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
            {availableModels.map(model => (
              <ModelCard
                key={model.id}
                model={model}
                onPull={() => pullModel.mutate({ modelId: model.id })}
                isPulling={pullModel.isPending}
              />
            ))}
          </div>
        </section>
      )}

      {/* Failed Models */}
      {failedModels.length > 0 && (
        <section>
          <h2 className="text-lg font-semibold mb-3 flex items-center gap-2">
            <AlertCircle className="h-4 w-4 text-red-500" />
            Failed ({failedModels.length})
          </h2>
          <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
            {failedModels.map(model => (
              <ModelCard
                key={model.id}
                model={model}
                onPull={() => pullModel.mutate({ modelId: model.id, force: true })}
                isPulling={pullModel.isPending}
              />
            ))}
          </div>
        </section>
      )}
    </div>
  );
}
```

### ModelCard Component

**Create `web/src/components/ModelCard.tsx`:**

```tsx
import { formatBytes, formatNumber } from '../lib/format';
import type { ModelRegistryEntry } from '../api/types';

interface ModelCardProps {
  model: ModelRegistryEntry;
  onPull?: () => void;
  onRemove?: () => void;
  isPulling?: boolean;
  isRemoving?: boolean;
}

export function ModelCard({ model, onPull, onRemove, isPulling, isRemoving }: ModelCardProps) {
  const statusColors: Record<string, string> = {
    ready: 'bg-green-500',
    downloading: 'bg-yellow-500 animate-pulse',
    not_downloaded: 'bg-gray-400',
    failed: 'bg-red-500',
  };

  return (
    <Card className="flex flex-col">
      <CardHeader className="pb-2">
        <div className="flex items-start justify-between">
          <div className="flex-1 min-w-0">
            <CardTitle className="text-base truncate">{model.id}</CardTitle>
            {model.name && (
              <p className="text-sm text-muted-foreground truncate">{model.name}</p>
            )}
          </div>
          <div className="flex items-center gap-2">
            <div className={cn("w-2 h-2 rounded-full", statusColors[model.status])} />
            {model.size_bytes && (
              <span className="text-xs text-muted-foreground">
                {formatBytes(model.size_bytes)}
              </span>
            )}
          </div>
        </div>
      </CardHeader>

      <CardContent className="flex-1 space-y-3">
        {/* Runtime & Stage */}
        <div className="flex flex-wrap gap-1.5">
          <Badge variant="secondary">{model.runtime}</Badge>
          <Badge variant="outline">{model.stage}</Badge>
        </div>

        {/* Capabilities */}
        <div className="flex flex-wrap gap-1.5">
          {model.word_timestamps && (
            <Badge variant="outline" className="text-xs">
              <Clock className="h-3 w-3 mr-1" /> timestamps
            </Badge>
          )}
          {model.punctuation && (
            <Badge variant="outline" className="text-xs">punctuation</Badge>
          )}
          {model.streaming && (
            <Badge variant="outline" className="text-xs">streaming</Badge>
          )}
          {model.supports_cpu && (
            <Badge variant="outline" className="text-xs">CPU</Badge>
          )}
        </div>

        {/* Languages */}
        {model.languages && model.languages.length > 0 && (
          <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
            <Globe className="h-3 w-3" />
            {model.languages.length > 5
              ? `${model.languages.slice(0, 5).join(', ')} +${model.languages.length - 5}`
              : model.languages.join(', ')}
          </div>
        )}

        {/* HF Stats */}
        {model.metadata?.downloads && (
          <div className="flex items-center gap-3 text-xs text-muted-foreground">
            <span className="flex items-center gap-1">
              <Download className="h-3 w-3" />
              {formatNumber(model.metadata.downloads)}
            </span>
            {model.metadata.likes && (
              <span className="flex items-center gap-1">
                <Heart className="h-3 w-3" />
                {formatNumber(model.metadata.likes)}
              </span>
            )}
          </div>
        )}

        {/* Hardware Requirements */}
        {(model.min_vram_gb || model.min_ram_gb) && (
          <div className="text-xs text-muted-foreground">
            {model.min_vram_gb && <span>VRAM: {model.min_vram_gb}GB</span>}
            {model.min_vram_gb && model.min_ram_gb && <span className="mx-1">•</span>}
            {model.min_ram_gb && <span>RAM: {model.min_ram_gb}GB</span>}
          </div>
        )}

        {/* Download Progress */}
        {model.status === 'downloading' && model.download_progress !== undefined && (
          <Progress value={model.download_progress} className="h-1" />
        )}

        {/* Error Message */}
        {model.status === 'failed' && model.metadata?.error && (
          <p className="text-xs text-red-500 truncate" title={model.metadata.error}>
            {model.metadata.error}
          </p>
        )}
      </CardContent>

      <CardFooter className="pt-2">
        <div className="flex items-center justify-between w-full">
          {model.source === 'huggingface' && model.runtime_model_id && (
            <a
              href={`https://huggingface.co/${model.runtime_model_id}`}
              target="_blank"
              rel="noopener noreferrer"
              className="text-xs text-muted-foreground hover:text-foreground flex items-center gap-1"
            >
              <ExternalLink className="h-3 w-3" />
              View on HF
            </a>
          )}
          <div className="flex gap-2 ml-auto">
            {model.status === 'ready' && onRemove && (
              <Button
                variant="outline"
                size="sm"
                onClick={onRemove}
                disabled={isRemoving}
              >
                {isRemoving ? <Loader2 className="h-3 w-3 animate-spin" /> : 'Remove'}
              </Button>
            )}
            {(model.status === 'not_downloaded' || model.status === 'failed') && onPull && (
              <Button
                size="sm"
                onClick={onPull}
                disabled={isPulling}
              >
                {isPulling ? <Loader2 className="h-3 w-3 animate-spin mr-1" /> : <Download className="h-3 w-3 mr-1" />}
                Pull
              </Button>
            )}
          </div>
        </div>
      </CardFooter>
    </Card>
  );
}
```

### ModelFiltersBar Component

**Create `web/src/components/ModelFiltersBar.tsx`:**

```tsx
interface ModelFiltersBarProps {
  filters: ModelFilters;
  onChange: (filters: ModelFilters) => void;
}

export function ModelFiltersBar({ filters, onChange }: ModelFiltersBarProps) {
  const stages = ['transcribe', 'align', 'diarize'];
  const runtimes = ['faster-whisper', 'nemo', 'whisperx', 'hf-asr'];
  const statuses = ['ready', 'downloading', 'not_downloaded', 'failed'];

  return (
    <div className="flex flex-wrap gap-3 items-center">
      <div className="relative flex-1 min-w-[200px] max-w-sm">
        <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
        <Input
          placeholder="Search models..."
          className="pl-9"
          value={filters.search || ''}
          onChange={(e) => onChange({ ...filters, search: e.target.value || undefined })}
        />
      </div>

      <Select
        value={filters.stage || ''}
        onValueChange={(v) => onChange({ ...filters, stage: v || undefined })}
      >
        <SelectTrigger className="w-[140px]">
          <SelectValue placeholder="All stages" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="">All stages</SelectItem>
          {stages.map(s => (
            <SelectItem key={s} value={s}>{s}</SelectItem>
          ))}
        </SelectContent>
      </Select>

      <Select
        value={filters.runtime || ''}
        onValueChange={(v) => onChange({ ...filters, runtime: v || undefined })}
      >
        <SelectTrigger className="w-[160px]">
          <SelectValue placeholder="All runtimes" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="">All runtimes</SelectItem>
          {runtimes.map(r => (
            <SelectItem key={r} value={r}>{r}</SelectItem>
          ))}
        </SelectContent>
      </Select>

      <Select
        value={filters.status || ''}
        onValueChange={(v) => onChange({ ...filters, status: (v as ModelStatus) || undefined })}
      >
        <SelectTrigger className="w-[150px]">
          <SelectValue placeholder="All statuses" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="">All statuses</SelectItem>
          {statuses.map(s => (
            <SelectItem key={s} value={s}>{s}</SelectItem>
          ))}
        </SelectContent>
      </Select>

      {(filters.search || filters.stage || filters.runtime || filters.status) && (
        <Button
          variant="ghost"
          size="sm"
          onClick={() => onChange({})}
        >
          Clear filters
        </Button>
      )}
    </div>
  );
}
```

### HFModelInput Component

**Create `web/src/components/HFModelInput.tsx`:**

```tsx
interface HFModelInputProps {
  onResolve: (modelId: string) => void;
  isLoading: boolean;
  result?: HFResolveResponse;
  error?: Error;
}

export function HFModelInput({ onResolve, isLoading, result, error }: HFModelInputProps) {
  const [modelId, setModelId] = useState('');

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (modelId.trim()) {
      onResolve(modelId.trim());
    }
  };

  return (
    <form onSubmit={handleSubmit} className="space-y-3">
      <div className="flex gap-2">
        <Input
          placeholder="e.g., nvidia/parakeet-tdt-1.1b or openai/whisper-large-v3"
          value={modelId}
          onChange={(e) => setModelId(e.target.value)}
          className="flex-1"
        />
        <Button type="submit" disabled={isLoading || !modelId.trim()}>
          {isLoading ? <Loader2 className="h-4 w-4 animate-spin" /> : 'Resolve'}
        </Button>
      </div>

      {result && (
        <div className={cn(
          "p-3 rounded-lg text-sm",
          result.resolved_runtime ? "bg-green-50 dark:bg-green-900/20" : "bg-yellow-50 dark:bg-yellow-900/20"
        )}>
          {result.resolved_runtime ? (
            <div className="space-y-1">
              <p className="font-medium text-green-700 dark:text-green-300">
                ✓ Resolved to runtime: {result.resolved_runtime}
              </p>
              {result.library_name && (
                <p className="text-muted-foreground">Library: {result.library_name}</p>
              )}
              {result.languages.length > 0 && (
                <p className="text-muted-foreground">
                  Languages: {result.languages.slice(0, 10).join(', ')}
                  {result.languages.length > 10 && ` +${result.languages.length - 10} more`}
                </p>
              )}
              <p className="text-muted-foreground">
                {formatNumber(result.downloads)} downloads • {formatNumber(result.likes)} likes
              </p>
            </div>
          ) : (
            <p className="text-yellow-700 dark:text-yellow-300">
              Could not determine runtime for this model. It may not be a supported ASR model.
            </p>
          )}
        </div>
      )}

      {error && (
        <p className="text-sm text-red-500">
          Error: {error.message}
        </p>
      )}
    </form>
  );
}
```

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

**Create `web/src/components/ModelSelector.tsx`:**

```tsx
interface ModelSelectorProps {
  value: string;
  onChange: (value: string) => void;
  language?: string; // Filter models by language compatibility
}

export function ModelSelector({ value, onChange, language }: ModelSelectorProps) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState('');
  const [customHF, setCustomHF] = useState('');

  const { data: registryData } = useModelRegistry({ stage: 'transcribe' });
  const resolveHF = useResolveHFModel();

  const models = registryData?.data ?? [];
  const readyModels = models.filter(m => m.status === 'ready');

  // Group by runtime
  const groupedModels = readyModels.reduce((acc, model) => {
    const runtime = model.runtime;
    if (!acc[runtime]) acc[runtime] = [];
    acc[runtime].push(model);
    return acc;
  }, {} as Record<string, ModelRegistryEntry[]>);

  // Filter by search and language
  const filteredGroups = Object.entries(groupedModels).map(([runtime, models]) => ({
    runtime,
    models: models.filter(m => {
      const matchesSearch = !search ||
        m.id.toLowerCase().includes(search.toLowerCase()) ||
        m.name?.toLowerCase().includes(search.toLowerCase());
      const matchesLanguage = !language ||
        !m.languages ||
        m.languages.includes(language);
      return matchesSearch && matchesLanguage;
    }),
  })).filter(g => g.models.length > 0);

  const selectedModel = models.find(m => m.id === value);

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          variant="outline"
          role="combobox"
          aria-expanded={open}
          className="w-full justify-between"
        >
          {value === 'auto' ? (
            <span className="flex items-center gap-2">
              <Sparkles className="h-4 w-4" />
              Auto (Recommended)
            </span>
          ) : selectedModel ? (
            <span className="flex items-center gap-2">
              <Badge variant="secondary" className="text-xs">{selectedModel.runtime}</Badge>
              {selectedModel.id}
            </span>
          ) : value ? (
            <span className="flex items-center gap-2">
              <Badge variant="outline" className="text-xs">custom</Badge>
              {value}
            </span>
          ) : (
            'Select model...'
          )}
          <ChevronsUpDown className="ml-2 h-4 w-4 shrink-0 opacity-50" />
        </Button>
      </PopoverTrigger>

      <PopoverContent className="w-[400px] p-0" align="start">
        <Command>
          <CommandInput
            placeholder="Search models..."
            value={search}
            onValueChange={setSearch}
          />

          <CommandList>
            <CommandEmpty>No models found.</CommandEmpty>

            {/* Auto option */}
            <CommandGroup heading="Recommended">
              <CommandItem
                value="auto"
                onSelect={() => {
                  onChange('auto');
                  setOpen(false);
                }}
              >
                <Sparkles className="mr-2 h-4 w-4" />
                <div className="flex-1">
                  <div className="font-medium">Auto</div>
                  <div className="text-xs text-muted-foreground">
                    Automatically select best model based on your settings
                  </div>
                </div>
                {value === 'auto' && <Check className="ml-2 h-4 w-4" />}
              </CommandItem>
            </CommandGroup>

            {/* Grouped models */}
            {filteredGroups.map(({ runtime, models }) => (
              <CommandGroup key={runtime} heading={runtime}>
                {models.map(model => (
                  <CommandItem
                    key={model.id}
                    value={model.id}
                    onSelect={() => {
                      onChange(model.id);
                      setOpen(false);
                    }}
                  >
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2">
                        <span className="font-medium truncate">{model.id}</span>
                        {model.size_bytes && (
                          <span className="text-xs text-muted-foreground">
                            {formatBytes(model.size_bytes)}
                          </span>
                        )}
                      </div>
                      <div className="flex items-center gap-2 text-xs text-muted-foreground">
                        {model.languages && (
                          <span>
                            {model.languages.length > 3
                              ? `${model.languages.length} languages`
                              : model.languages.join(', ')}
                          </span>
                        )}
                        {model.word_timestamps && <span>• timestamps</span>}
                      </div>
                    </div>
                    {value === model.id && <Check className="ml-2 h-4 w-4" />}
                  </CommandItem>
                ))}
              </CommandGroup>
            ))}

            {/* Help link to Models page */}
            <div className="border-t mt-2 pt-2 px-3 pb-1">
              <p className="text-xs text-muted-foreground">
                Register more models on the{' '}
                <a href="/models" className="underline hover:text-foreground">
                  Models page
                </a>
              </p>
            </div>
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  );
}
```

### Update NewJob.tsx

Replace the existing model Select with the new ModelSelector:

```tsx
// In NewJob.tsx
import { ModelSelector } from '../components/ModelSelector';

// Replace:
// <Select value={model} onValueChange={setModel}>
//   ...
// </Select>

// With:
<div className="space-y-2">
  <Label>Model</Label>
  <ModelSelector
    value={model}
    onChange={setModel}
    language={language}
  />
  {model && model !== 'auto' && (
    <ModelCompatibilityWarning modelId={model} language={language} />
  )}
</div>
```

### ModelCompatibilityWarning Component

**Create `web/src/components/ModelCompatibilityWarning.tsx`:**

```tsx
interface ModelCompatibilityWarningProps {
  modelId: string;
  language?: string;
}

export function ModelCompatibilityWarning({ modelId, language }: ModelCompatibilityWarningProps) {
  const { data: model } = useModelRegistryEntry(modelId);

  if (!model) return null;

  const warnings: string[] = [];

  // Check download status
  if (model.status === 'not_downloaded') {
    warnings.push('This model is not downloaded. It will be downloaded when the job starts.');
  } else if (model.status === 'downloading') {
    warnings.push('This model is currently downloading.');
  } else if (model.status === 'failed') {
    warnings.push('This model failed to download. The job may fail.');
  }

  // Check language compatibility
  if (language && model.languages && !model.languages.includes(language)) {
    warnings.push(`This model may not support ${language}. Supported: ${model.languages.join(', ')}`);
  }

  if (warnings.length === 0) return null;

  return (
    <div className="space-y-1">
      {warnings.map((warning, i) => (
        <p key={i} className="text-sm text-yellow-600 dark:text-yellow-400 flex items-center gap-1">
          <AlertTriangle className="h-3 w-3" />
          {warning}
        </p>
      ))}
    </div>
  );
}
```

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

### Update Engines Page

Enhance the engine cards to show model information:

```tsx
// In Engines.tsx - Update the engine card rendering

<Card key={engine.engine_id}>
  <CardHeader className="pb-2">
    <div className="flex items-center justify-between">
      <CardTitle className="text-base">{engine.engine_id}</CardTitle>
      <EngineStatusIndicator status={engine.status} />
    </div>
  </CardHeader>

  <CardContent className="space-y-3">
    {/* Existing queue stats */}
    <div className="flex items-center gap-4 text-sm text-muted-foreground">
      <span>Queue: {engine.queue_depth}</span>
      <span>Processing: {engine.processing}</span>
    </div>

    {/* NEW: Loaded Model */}
    {engine.loaded_model && (
      <div className="flex items-center gap-2">
        <Badge variant="default" className="text-xs">
          <Cpu className="h-3 w-3 mr-1" />
          {engine.loaded_model}
        </Badge>
      </div>
    )}

    {/* NEW: Available Models */}
    {engine.available_models && engine.available_models.length > 0 && (
      <div className="space-y-1">
        <p className="text-xs text-muted-foreground">Available models:</p>
        <div className="flex flex-wrap gap-1">
          {engine.available_models.slice(0, 5).map(modelId => (
            <Badge key={modelId} variant="outline" className="text-xs">
              {modelId}
            </Badge>
          ))}
          {engine.available_models.length > 5 && (
            <Badge variant="outline" className="text-xs">
              +{engine.available_models.length - 5} more
            </Badge>
          )}
        </div>
      </div>
    )}
  </CardContent>
</Card>
```

### Backend Enhancement

Ensure `/v1/engines` returns `loaded_model` and `available_models`:

```python
# This should already be returned by the engines endpoint per M40
# Verify the response includes:
{
  "engine_id": "stt-batch-transcribe-faster-whisper",
  "stage": "transcribe",
  "status": "running",
  "queue_depth": 0,
  "processing": 0,
  "loaded_model": "whisper-large-v3",  # Currently loaded
  "available_models": ["whisper-large-v3", "whisper-medium", ...]  # On disk
}
```

### Deliverables

- [x] Engine cards show currently loaded model
- [x] Engine cards show available models (collapsed view with +N more)
- [x] Click to expand full model list (badge overflow)
- [ ] Link to model detail from engine card

---

## Phase 42.4: Download Progress UI

**Goal:** Global notification system for model downloads.

### Download Progress Context

**Create `web/src/contexts/DownloadContext.tsx`:**

```tsx
interface Download {
  modelId: string;
  status: 'downloading' | 'completed' | 'failed';
  progress?: number;
  error?: string;
  startedAt: Date;
}

interface DownloadContextValue {
  downloads: Download[];
  activeCount: number;
}

export const DownloadContext = createContext<DownloadContextValue>({
  downloads: [],
  activeCount: 0,
});

export function DownloadProvider({ children }: { children: React.ReactNode }) {
  const { data } = useModelRegistry({ status: 'downloading' });

  const downloads: Download[] = (data?.data ?? []).map(m => ({
    modelId: m.id,
    status: 'downloading',
    progress: m.download_progress,
    startedAt: new Date(m.created_at),
  }));

  return (
    <DownloadContext.Provider value={{ downloads, activeCount: downloads.length }}>
      {children}
    </DownloadContext.Provider>
  );
}

export function useDownloads() {
  return useContext(DownloadContext);
}
```

### DownloadIndicator Component

**Create `web/src/components/DownloadIndicator.tsx`:**

```tsx
export function DownloadIndicator() {
  const { downloads, activeCount } = useDownloads();

  if (activeCount === 0) return null;

  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button variant="ghost" size="sm" className="relative">
          <Download className="h-4 w-4" />
          <span className="absolute -top-1 -right-1 h-4 w-4 rounded-full bg-primary text-[10px] text-primary-foreground flex items-center justify-center">
            {activeCount}
          </span>
        </Button>
      </PopoverTrigger>

      <PopoverContent className="w-80" align="end">
        <div className="space-y-3">
          <h4 className="font-medium text-sm">Downloading Models</h4>
          {downloads.map(download => (
            <div key={download.modelId} className="space-y-1">
              <div className="flex items-center justify-between text-sm">
                <span className="truncate">{download.modelId}</span>
                {download.progress !== undefined && (
                  <span className="text-muted-foreground">{download.progress}%</span>
                )}
              </div>
              <Progress value={download.progress ?? 0} className="h-1" />
            </div>
          ))}
        </div>
      </PopoverContent>
    </Popover>
  );
}
```

### Integration in Layout

```tsx
// In Layout.tsx header area
<div className="flex items-center gap-2">
  <DownloadIndicator />
  {/* other header items */}
</div>
```

### Toast Notifications

Add toast notifications for download events:

```tsx
// In useModelRegistry.ts or a separate effect hook
export function useDownloadNotifications() {
  const queryClient = useQueryClient();

  useEffect(() => {
    // Poll for status changes and show toasts
    const interval = setInterval(async () => {
      const prev = queryClient.getQueryData<{ data: ModelRegistryEntry[] }>(['modelRegistry']);
      await queryClient.invalidateQueries({ queryKey: ['modelRegistry'] });
      const next = queryClient.getQueryData<{ data: ModelRegistryEntry[] }>(['modelRegistry']);

      // Check for status transitions
      prev?.data?.forEach(prevModel => {
        const nextModel = next?.data?.find(m => m.id === prevModel.id);
        if (nextModel) {
          if (prevModel.status === 'downloading' && nextModel.status === 'ready') {
            toast.success(`Model ${prevModel.id} downloaded successfully`);
          } else if (prevModel.status === 'downloading' && nextModel.status === 'failed') {
            toast.error(`Model ${prevModel.id} download failed`);
          }
        }
      });
    }, 5000);

    return () => clearInterval(interval);
  }, [queryClient]);
}
```

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

**Create `web/src/hooks/useCapabilities.ts`:**

```typescript
export interface SystemCapabilities {
  languages: string[];
  features: {
    word_timestamps: boolean;
    speaker_diarization: boolean;
    pii_detection: boolean;
    streaming: boolean;
  };
  engines_by_stage: Record<string, number>;
  models_ready: number;
  models_total: number;
}

export function useSystemCapabilities() {
  return useQuery({
    queryKey: ['capabilities'],
    queryFn: async (): Promise<SystemCapabilities> => {
      const response = await apiClient.get('v1/capabilities');
      return response.json();
    },
    staleTime: 60_000,
  });
}
```

### CapabilitiesCard Component

**Create `web/src/components/CapabilitiesCard.tsx`:**

```tsx
export function CapabilitiesCard() {
  const { data: capabilities, isLoading } = useSystemCapabilities();

  if (isLoading) {
    return <Card><CardContent className="p-6"><Skeleton className="h-32" /></CardContent></Card>;
  }

  if (!capabilities) return null;

  const features = [
    { key: 'word_timestamps', label: 'Word Timestamps', icon: Clock },
    { key: 'speaker_diarization', label: 'Speaker Diarization', icon: Users },
    { key: 'pii_detection', label: 'PII Detection', icon: Shield },
    { key: 'streaming', label: 'Real-time Streaming', icon: Radio },
  ];

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-base flex items-center gap-2">
          <Zap className="h-4 w-4" />
          System Capabilities
        </CardTitle>
      </CardHeader>

      <CardContent className="space-y-4">
        {/* Languages */}
        <div>
          <p className="text-sm text-muted-foreground mb-1">Languages</p>
          <div className="flex items-center gap-2">
            <Globe className="h-4 w-4 text-muted-foreground" />
            <span className="text-sm font-medium">
              {capabilities.languages.length} supported
            </span>
            {capabilities.languages.length > 0 && (
              <span className="text-xs text-muted-foreground">
                ({capabilities.languages.slice(0, 5).join(', ')}
                {capabilities.languages.length > 5 && '...'})
              </span>
            )}
          </div>
        </div>

        {/* Features */}
        <div>
          <p className="text-sm text-muted-foreground mb-2">Features</p>
          <div className="grid grid-cols-2 gap-2">
            {features.map(({ key, label, icon: Icon }) => (
              <div
                key={key}
                className={cn(
                  "flex items-center gap-2 text-sm",
                  capabilities.features[key as keyof typeof capabilities.features]
                    ? "text-foreground"
                    : "text-muted-foreground"
                )}
              >
                {capabilities.features[key as keyof typeof capabilities.features] ? (
                  <CheckCircle className="h-4 w-4 text-green-500" />
                ) : (
                  <XCircle className="h-4 w-4 text-muted-foreground" />
                )}
                {label}
              </div>
            ))}
          </div>
        </div>

        {/* Models */}
        <div className="flex items-center justify-between text-sm">
          <span className="text-muted-foreground">Models Ready</span>
          <span className="font-medium">
            {capabilities.models_ready} / {capabilities.models_total}
          </span>
        </div>

        <Button variant="outline" size="sm" className="w-full" asChild>
          <Link to="/models">
            View All Models
            <ArrowRight className="h-4 w-4 ml-2" />
          </Link>
        </Button>
      </CardContent>
    </Card>
  );
}
```

### Integration in Dashboard

```tsx
// In Dashboard.tsx - Add to the stats grid
<div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
  <SystemStatusCard />
  <BatchQueueCard />
  <RealtimeCapacityCard />
  <CapabilitiesCard />  {/* NEW */}
</div>
```

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

## File Changes Summary

### New Files

```text
web/src/pages/
├── Models.tsx                    # Model registry page

web/src/components/
├── ModelCard.tsx                 # Individual model display
├── ModelFiltersBar.tsx           # Filters for model list
├── ModelSelector.tsx             # Enhanced job creation selector
├── ModelCompatibilityWarning.tsx # Warnings in job creation
├── HFModelInput.tsx              # HuggingFace model input
├── CapabilitiesCard.tsx          # Dashboard capabilities
├── DownloadIndicator.tsx         # Header download status

web/src/hooks/
├── useModelRegistry.ts           # Model registry queries/mutations
├── useCapabilities.ts            # System capabilities query

web/src/contexts/
├── DownloadContext.tsx           # Download progress tracking
```

### Modified Files

```text
web/src/App.tsx                   # Add /models route
web/src/components/Sidebar.tsx    # Add Models nav item
web/src/components/Layout.tsx     # Add DownloadIndicator
web/src/pages/NewJob.tsx          # Replace model dropdown
web/src/pages/Dashboard.tsx       # Add CapabilitiesCard
web/src/pages/Engines.tsx         # Add model visibility
web/src/api/client.ts             # Add model registry methods
web/src/api/types.ts              # Add model registry types
```

---

## Verification

```bash
# Start dev environment
make dev

# Build and serve web console
cd web && pnpm dev

# Test Model Registry Page
open http://localhost:5173/models
# - Verify models load with correct status
# - Test search and filters
# - Pull a model and verify progress
# - Remove a model and verify it moves to "Available"

# Test HuggingFace Resolution
# Enter "nvidia/parakeet-tdt-1.1b" in the HF input
# - Should resolve to "nemo" runtime
# - Should show metadata (downloads, likes)

# Test Enhanced Job Creation
open http://localhost:5173/jobs/new
# - Model selector shows grouped models
# - Search filters correctly
# - Auto option is available
# - Custom HF input works
# - Warnings show for incompatible models

# Test Engine Model Visibility
open http://localhost:5173/engines
# - Engine cards show loaded model
# - Available models shown as badges

# Test Download Progress
# Pull a model from the Models page
# - Download indicator appears in header
# - Progress updates
# - Toast on completion

# Test Dashboard Capabilities
open http://localhost:5173/
# - Capabilities card shows language count
# - Features show enabled/disabled
# - Models ready count is accurate
```

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
| Auto model selection fails with missing model | ✅ **Fixed**: Orchestrator queries registry for downloaded models instead of hardcoded fallback; raises `NoDownloadedModelError` with clear message if none available |

---

## Future Considerations

Not in scope for M42:

- **Model comparison**: Side-by-side capability comparison
- **Model benchmarks**: Show RTF, accuracy metrics
- **Batch model download**: Download multiple models at once
- **Model presets**: Save favorite model configurations
- **Usage analytics**: Track which models are most used

**Next**: M43 (to be determined)
