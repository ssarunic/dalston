# M40: Model Registry & HuggingFace Integration

|                  |                                                                    |
| ---------------- | ------------------------------------------------------------------ |
| **Goal**         | PostgreSQL-backed model registry with download management and HF auto-routing |
| **Duration**     | 4 days                                                             |
| **Dependencies** | M36 (Runtime Model Management)                                     |
| **Deliverable**  | Model registry API, download workflow, HF resolution, web console  |
| **Status**       | **COMPLETE**                                                       |

## Overview

Transformed model management from static JSON catalog to dynamic PostgreSQL registry with:

1. **Model Registry**: Track downloaded models, status, and metadata in database
2. **S3 Storage**: Models stored in S3 as canonical source, engines cache locally
3. **Download Workflow**: Pull models from HuggingFace, upload to S3
4. **HuggingFace Auto-Routing**: Auto-detect engine from model's `library_name`
5. **Web Console**: Full model management UI with download/remove/sync

---

## Implementation Summary

### 40.1: Model Registry Database (COMPLETE)

**Migration**: `alembic/versions/20260302_0024_create_models_table.py`

**Database Schema** (`models` table):

| Column | Type | Description |
|--------|------|-------------|
| `id` | String(200) | Namespaced model ID (PK) |
| `name` | String(200) | Human-readable name |
| `engine_id` | String(50) | Engine engine_id |
| `loaded_model_id` | String(200) | HF model ID for engine |
| `stage` | String(50) | Pipeline stage |
| `status` | String(20) | not_downloaded, downloading, ready, failed |
| `download_path` | Text | S3 URI |
| `size_bytes` | BigInteger | Downloaded size |
| `downloaded_at` | Timestamp | When downloaded |
| `source` | String(200) | HuggingFace repo ID |
| `library_name` | String(50) | ML library |
| `languages` | JSONB | Language codes |
| `word_timestamps` | Boolean | Capability |
| `punctuation` | Boolean | Capability |
| `capitalization` | Boolean | Capability |
| `streaming` | Boolean | Capability |
| `min_vram_gb` | Float | Hardware requirement |
| `min_ram_gb` | Float | Hardware requirement |
| `supports_cpu` | Boolean | Hardware capability |
| `model_metadata` | JSONB | HF card data (downloads, likes, tags, error) |
| `last_used_at` | Timestamp | Usage tracking |
| `created_at` | Timestamp | Record creation |
| `updated_at` | Timestamp | Last modification |

**Subsequent Migrations**:

- `0025`: Added `capitalization` column
- `0026`: Migrated to namespaced IDs (`parakeet-tdt-1.1b` → `nvidia/parakeet-tdt-1.1b`)
- `0027`: Extended `source` column from String(50) to String(200)

---

### 40.2: Model Registry Service (COMPLETE)

**File**: `dalston/gateway/services/model_registry.py`

| Method | Description |
|--------|-------------|
| `get_model(db, model_id)` | Lookup by ID |
| `get_model_or_raise(...)` | Same, raises `ModelNotFoundError` |
| `list_models(db, stage, engine_id, status)` | Filtered listing |
| `register_model(db, ...)` | Create new registry entry |
| `pull_model(db, model_id, force)` | Download HF → S3, update status |
| `remove_model(db, model_id, purge)` | Remove S3 files; optionally delete row |
| `sync_from_s3(db)` | Reconcile DB status with S3 state |
| `touch_model(db, model_id)` | Update `last_used_at` |
| `seed_from_catalog(db, update_existing)` | Populate DB from static catalog |
| `resolve_hf_model(db, hf_model_id, auto_register)` | Fetch HF metadata, determine engine_id |
| `get_or_resolve_model(db, model_id)` | Try DB first, fall back to HF resolution |
| `ensure_ready(db, model_id)` | Validate model is downloaded |

**Status Flow**:

```
not_downloaded → downloading → ready
                     ↓
                  failed
```

**S3 Storage Structure**:

```
s3://dalston-artifacts/
└── models/
    └── nvidia/parakeet-tdt-1.1b/
        ├── model.nemo
        ├── config.yaml
        └── .complete           # Atomic completion marker
```

---

### 40.3: API Endpoints (COMPLETE)

**File**: `dalston/gateway/api/v1/models.py`

| Method | Route | Description |
|--------|-------|-------------|
| `GET` | `/v1/models` | List static catalog models |
| `GET` | `/v1/models/{model_id}` | Get single catalog entry |
| `GET` | `/v1/models/registry` | List DB registry with status |
| `GET` | `/v1/models/registry/{model_id}` | Get single registry entry |
| `POST` | `/v1/models/{model_id}/pull` | Start background download |
| `DELETE` | `/v1/models/{model_id}` | Remove files (purge=true deletes entry) |
| `POST` | `/v1/models/sync` | Reconcile DB with S3 |
| `POST` | `/v1/models/hf/resolve` | Resolve HF model to engine_id |
| `GET` | `/v1/models/hf/mappings` | Get routing mappings |

**Download Protection**: `remove_model` checks for pending/processing jobs before deletion; raises `ModelInUseError` if in use.

---

### 40.4: HuggingFace Auto-Routing (COMPLETE)

**File**: `dalston/gateway/services/hf_resolver.py`

**Routing Priority**:

1. **`library_name`** (most reliable):
   - `ctranslate2` → `faster-whisper`
   - `nemo` → `nemo`
   - `transformers` + ASR pipeline → `hf-asr`
   - `vllm` → `vllm-asr`

2. **Tags** (fallback):
   - `faster-whisper`, `ctranslate2` → `faster-whisper`
   - `nemo` → `nemo`
   - `whisper` → `faster-whisper`

3. **`pipeline_tag`** (last resort):
   - `automatic-speech-recognition` → `hf-asr`

**Example Resolution**:

```json
// POST /v1/models/hf/resolve
// Request: {"model_id": "openai/whisper-large-v3", "auto_register": true}
// Response:
{
  "model_id": "openai/whisper-large-v3",
  "library_name": "ctranslate2",
  "pipeline_tag": "automatic-speech-recognition",
  "resolved_engine_id": "faster-whisper",
  "can_route": true,
  "downloads": 1500000,
  "likes": 3200
}
```

---

### 40.5: Web Console Model Management (COMPLETE)

**Files**:

- `web/src/pages/Models.tsx` - Main page
- `web/src/components/ModelTable.tsx` - Registry table with expand
- `web/src/components/ModelFiltersBar.tsx` - Filter controls
- `web/src/components/AddModelDialog.tsx` - HF model add dialog
- `web/src/components/HFModelInput.tsx` - Live HF search input
- `web/src/hooks/useModelRegistry.ts` - React Query hooks
- `web/src/api/client.ts` - API client methods
- `web/src/api/types.ts` - TypeScript types

**Features**:

1. **Model Registry Table**
   - Columns: Model ID, Runtime, Status, Size, Capabilities, Actions
   - Expandable rows with hardware requirements, languages, HF metadata
   - Status indicators (green=ready, yellow=downloading, gray=available, red=failed)

2. **Actions per Model**
   - Download (not_downloaded/failed) → `POST /v1/models/{id}/pull`
   - Remove files (ready) → `DELETE /v1/models/{id}`
   - Delete from registry → `DELETE /v1/models/{id}?purge=true`

3. **Filters**
   - Text search across ID, name, engine_id
   - Stage dropdown (transcribe, diarize, align)
   - Runtime dropdown (faster-whisper, nemo, etc.)
   - Status dropdown (ready, not_downloaded, downloading, failed)

4. **Add from HuggingFace**
   - Live search of HF Hub API with 300ms debounce
   - Autocomplete dropdown with download counts
   - Auto-resolve engine_id on submit
   - Auto-register to database

5. **Sync with Disk**
   - Button to reconcile DB with S3 state
   - Detects models downloaded by engines directly

**React Query Hooks**:

| Hook | Purpose |
|------|---------|
| `useModelRegistry(filters)` | Fetch registry; auto-poll while downloading |
| `usePullModel()` | Mutation to start download |
| `useRemoveModel()` | Mutation to remove files |
| `usePurgeModel()` | Mutation to delete from registry |
| `useResolveHFModel()` | Mutation to resolve HF model |
| `useSyncModels()` | Mutation to sync with S3 |

---

### 40.6: Audit Logging (COMPLETE)

**File**: `dalston/common/audit.py`

| Event | Data Logged |
|-------|-------------|
| `model.downloaded` | model_id, source, size_bytes, download_path |
| `model.removed` | model_id, download_path |
| `model.download_failed` | model_id, error |
| `model.deleted_from_registry` | model_id, download_path |

---

## Files Summary

### New Files

| File | Description |
|------|-------------|
| `alembic/versions/20260302_0024_create_models_table.py` | Database migration |
| `alembic/versions/..._0025_add_capitalization.py` | Add capitalization column |
| `alembic/versions/..._0026_migrate_to_namespaced_ids.py` | ID format migration |
| `alembic/versions/..._0027_extend_source_column.py` | Extend source length |
| `dalston/gateway/services/model_registry.py` | Registry service |
| `dalston/gateway/services/hf_resolver.py` | HuggingFace resolution |
| `web/src/pages/Models.tsx` | Console models page |
| `web/src/components/ModelTable.tsx` | Registry table |
| `web/src/components/ModelFiltersBar.tsx` | Filters UI |
| `web/src/components/AddModelDialog.tsx` | Add model dialog |
| `web/src/components/HFModelInput.tsx` | HF search input |
| `web/src/hooks/useModelRegistry.ts` | React Query hooks |

### Modified Files

| File | Change |
|------|--------|
| `dalston/db/models.py` | Add ModelRegistryModel ORM class |
| `dalston/gateway/api/v1/models.py` | Add registry and HF endpoints |
| `dalston/common/audit.py` | Add model audit events |
| `web/src/api/client.ts` | Add model API methods |
| `web/src/api/types.ts` | Add TypeScript types |
| `web/src/App.tsx` | Add /models route |

---

## Verification

### Database

```bash
# Run migrations
alembic upgrade head

# Seed registry from catalog
python -c "
from dalston.db.session import async_session
from dalston.gateway.services.model_registry import ModelRegistryService
import asyncio

async def seed():
    async with async_session() as db:
        service = ModelRegistryService()
        result = await service.seed_from_catalog(db)
        print(result)

asyncio.run(seed())
"
# Output: {'created': 18, 'updated': 0, 'skipped': 0}
```

### API

```bash
# List registry
curl http://localhost:8000/v1/models/registry | jq '.data | length'
# 18

# Pull a model
curl -X POST http://localhost:8000/v1/models/nvidia%2Fparakeet-tdt-1.1b/pull
# {"message": "Download started", "model_id": "nvidia/parakeet-tdt-1.1b", "status": "downloading"}

# Resolve HF model
curl -X POST http://localhost:8000/v1/models/hf/resolve \
  -H "Content-Type: application/json" \
  -d '{"model_id": "openai/whisper-large-v3", "auto_register": true}'
# {"resolved_engine_id": "faster-whisper", "can_route": true, ...}
```

### Web Console

1. Navigate to <http://localhost:5173/models>
2. Verify model table loads with status indicators
3. Click "Add from HF" → search "whisper" → select model
4. Verify model appears in table with "not_downloaded" status
5. Click Download → verify status changes to "downloading" then "ready"
6. Click Remove → verify files removed, status reverts

---

## Checkpoint Summary

- [x] **40.1**: `models` table migration applied
- [x] **40.2**: ModelRegistryService with all CRUD operations
- [x] **40.3**: API endpoints for catalog, registry, download, sync
- [x] **40.4**: HuggingFace auto-routing by library_name/tags
- [x] **40.5**: Web console with table, filters, add dialog
- [x] **40.6**: Audit logging for model operations
- [x] Job creation validates model is downloaded
- [x] Error messages guide user to download

---

## Related Milestones

- **M36**: Runtime Model Management (prerequisite)
- **M39**: Model Cache & TTL Management (engine-side caching)
