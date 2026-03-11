# M46: Model Registry as Source of Truth

| | |
|---|---|
| **Goal** | Make database registry the single source of truth for model metadata with user enrichment |
| **Duration** | 3-4 days |
| **Dependencies** | M40 (Model Registry & HuggingFace Integration) |
| **Deliverable** | Auto-seeding on startup, user enrichment API, orchestrator DB integration |
| **Status** | Planned |

## Problem Statement

Currently, model metadata exists in two places:

1. **YAML files** (`models/*.yaml`) - version-controlled, rich metadata (languages, capabilities, hardware requirements, performance metrics)
2. **Database** (`models` table) - tracks download status, but metadata only populated via manual `dalston model seed`

The flow is fragmented:

```
YAML → generate_catalog.py → JSON → manual seed_from_catalog() → DB
```

**Issues**:

- HuggingFace auto-resolved models lack critical info (`word_timestamps`, `punctuation`, `rtf_gpu`)
- Users cannot enrich model metadata (add notes, correct capabilities)
- Orchestrator uses static JSON catalog, not DB
- Manual seeding step easily forgotten after adding new models

## Solution

Make the database registry the single engine_id source of truth:

1. **Auto-seed on gateway startup** - Load YAMLs directly, upsert to DB
2. **Preserve user edits** - Track `metadata_source` to avoid overwriting enrichments
3. **User enrichment API** - PATCH endpoint to update model metadata
4. **Orchestrator uses DB** - Replace `catalog.get_model()` with DB queries

```
┌─────────────────┐      ┌─────────────────┐
│  models/*.yaml  │─────▶│    Database     │◀──── User enrichment (PATCH API)
│ (factory defaults)      │ (source of truth)
└─────────────────┘      └────────┬────────┘
                                  │
        ┌─────────────────────────┼─────────────────────────┐
        ▼                         ▼                         ▼
   Gateway API            Orchestrator              Web Console
```

---

## Implementation

### 46.1: Schema Migration

**Add `metadata_source` column to track provenance**

File: `alembic/versions/xxx_add_metadata_source.py`

```python
def upgrade():
    op.add_column(
        'models',
        sa.Column('metadata_source', sa.String(20), nullable=False, server_default='yaml')
    )

def downgrade():
    op.drop_column('models', 'metadata_source')
```

Values:

- `yaml` - Populated from YAML files (can be updated on re-seed)
- `user` - Manually enriched (preserved across re-seeds)
- `hf` - Auto-resolved from HuggingFace (can be enriched by user)

File: `dalston/db/models.py`

```python
class ModelRegistryModel(Base):
    # ... existing columns ...
    metadata_source: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="yaml"
    )
```

---

### 46.2: YAML Loader Module

**Create direct YAML loading (bypass JSON generation)**

File: `dalston/gateway/services/model_yaml_loader.py` (new)

```python
from pathlib import Path
from dataclasses import dataclass
import yaml

@dataclass
class ModelYAMLEntry:
    """Parsed model YAML entry."""
    id: str
    engine_id: str
    loaded_model_id: str
    name: str
    source: str | None
    size_gb: float | None
    stage: str
    languages: list[str] | None
    word_timestamps: bool
    punctuation: bool
    capitalization: bool
    streaming: bool
    min_vram_gb: float | None
    min_ram_gb: float | None
    supports_cpu: bool
    rtf_gpu: float | None
    rtf_cpu: float | None

def load_model_yamls(models_dir: Path | None = None) -> list[ModelYAMLEntry]:
    """Load and validate all model YAML files.

    Args:
        models_dir: Directory containing model YAMLs. Defaults to repo/models/

    Returns:
        List of parsed model entries

    Raises:
        ValueError: If any YAML is invalid (fail-closed)
    """
    if models_dir is None:
        models_dir = Path(__file__).parents[4] / "models"

    entries = []
    for yaml_path in sorted(models_dir.glob("*.yaml")):
        with open(yaml_path) as f:
            data = yaml.safe_load(f)
        entries.append(_parse_model_yaml(data, yaml_path))

    return entries

def _parse_model_yaml(data: dict, path: Path) -> ModelYAMLEntry:
    """Parse and validate a single model YAML."""
    # Validation and parsing logic
    # Reuse patterns from scripts/generate_catalog.py
```

---

### 46.3: Startup Seeding

**Modify `seed_from_yamls()` to preserve user edits**

File: `dalston/gateway/services/model_registry.py`

```python
async def seed_from_yamls(
    self,
    db: AsyncSession,
    *,
    models_dir: Path | None = None,
) -> dict[str, int]:
    """Seed registry from YAML files, preserving user-modified entries.

    For each YAML model:
    - If not in DB: INSERT with metadata_source="yaml"
    - If in DB with metadata_source="yaml": UPDATE all fields
    - If in DB with metadata_source="user": SKIP (preserve user edits)
    - If in DB with metadata_source="hf": UPDATE (improve HF-resolved data)

    Returns:
        Dict with counts: {"created": N, "updated": N, "skipped": N, "preserved": N}
    """
    from dalston.gateway.services.model_yaml_loader import load_model_yamls

    entries = load_model_yamls(models_dir)
    result = {"created": 0, "updated": 0, "skipped": 0, "preserved": 0}

    for entry in entries:
        existing = await self.get_model(db, entry.id)

        if existing is None:
            # New model - insert
            await self._insert_from_yaml(db, entry)
            result["created"] += 1
        elif existing.metadata_source == "user":
            # User-modified - preserve
            result["preserved"] += 1
        else:
            # yaml or hf - update
            await self._update_from_yaml(db, existing.id, entry)
            result["updated"] += 1

    await db.commit()
    return result
```

**Wire into gateway startup**

File: `dalston/gateway/main.py`

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    # ... existing startup code ...

    # Initialize database
    await init_db()

    # Seed model registry from YAMLs
    logger.info("Seeding model registry from YAMLs...")
    async with async_session() as db:
        service = ModelRegistryService()
        result = await service.seed_from_yamls(db)
        logger.info(
            "model_registry_seeded",
            created=result["created"],
            updated=result["updated"],
            preserved=result["preserved"],
        )

    # ... rest of startup ...
```

---

### 46.4: Orchestrator DB Integration

**Replace `catalog.get_model()` with DB query**

File: `dalston/orchestrator/engine_selector.py`

The `select_engine()` function already has `db: AsyncSession` parameter. Change model lookup:

```python
# Before (line ~409):
model = catalog.get_model(user_preference)

# After:
from dalston.db.models import ModelRegistryModel

result = await db.execute(
    select(ModelRegistryModel).where(ModelRegistryModel.id == user_preference)
)
db_model = result.scalar_one_or_none()

if db_model is not None:
    # Convert to format expected by rest of function
    model = _db_model_to_catalog_entry(db_model)
```

**Note**: Keep `EngineCatalog` for engine lookups (prepare, align, diarize, merge stages). Only model lookups migrate to DB.

---

### 46.5: User Enrichment API

**Add PATCH endpoint for model metadata**

File: `dalston/gateway/api/v1/models.py`

```python
class UpdateModelRequest(BaseModel):
    """Request body for updating model metadata."""
    name: str | None = None
    languages: list[str] | None = None
    word_timestamps: bool | None = None
    punctuation: bool | None = None
    capitalization: bool | None = None
    streaming: bool | None = None
    min_vram_gb: float | None = None
    min_ram_gb: float | None = None
    supports_cpu: bool | None = None
    rtf_gpu: float | None = None
    rtf_cpu: float | None = None

@router.patch(
    "/{model_id:path}",
    response_model=ModelRegistryResponse,
    summary="Update model metadata",
    description="Update user-editable model metadata. Sets metadata_source to 'user' to preserve edits across re-seeding.",
)
async def update_model(
    model_id: str,
    request: UpdateModelRequest,
    principal: Annotated[Principal, Depends(get_principal)],
    db: AsyncSession = Depends(get_db),
) -> ModelRegistryResponse:
    """Update model metadata and mark as user-modified."""
    service = ModelRegistryService()
    model = await service.update_model(
        db,
        model_id,
        updates=request.model_dump(exclude_unset=True),
        updated_by=principal.key_id,
    )
    return _to_response(model)
```

File: `dalston/gateway/services/model_registry.py`

```python
async def update_model(
    self,
    db: AsyncSession,
    model_id: str,
    updates: dict,
    updated_by: str | None = None,
) -> ModelRegistryModel:
    """Update model metadata and set metadata_source to 'user'."""
    model = await self.get_model_or_raise(db, model_id)

    for key, value in updates.items():
        if hasattr(model, key):
            setattr(model, key, value)

    model.metadata_source = "user"
    await db.commit()
    await db.refresh(model)

    # Audit log
    audit = get_audit_service()
    await audit.log_model_updated(
        model_id=model_id,
        updates=updates,
        updated_by=updated_by,
    )

    return model
```

---

### 46.6: Unify API Endpoints

**Merge `/v1/models` and `/v1/models/registry`**

Currently there are two separate endpoints:

- `GET /v1/models` - Returns static catalog data
- `GET /v1/models/registry` - Returns DB registry data

After this change, `GET /v1/models` will return DB registry data (the unified source of truth).

File: `dalston/gateway/api/v1/models.py`

```python
@router.get(
    "",
    response_model=ModelListResponse,
    summary="List all models",
)
async def list_models(
    stage: str | None = Query(None),
    engine_id: str | None = Query(None),
    status: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
) -> ModelListResponse:
    """List all models from the registry."""
    service = ModelRegistryService()
    models = await service.list_models(db, stage=stage, engine_id=engine_id, status=status)
    return ModelListResponse(data=[_to_response(m) for m in models])
```

**Remove** `/v1/models/registry` endpoint entirely - `GET /v1/models` is now the only endpoint.

---

### 46.7: Legacy Code Removal

**This is critical - remove ALL legacy model catalog code after new logic works.**

#### 46.7.1: Remove model methods from `EngineCatalog`

File: `dalston/orchestrator/catalog.py`

Delete these methods entirely:

- `get_model()`
- `get_all_models()`
- `get_engine_id_for_model()`
- `get_loaded_model_id()`
- `get_models_for_engine_id()`
- `get_models_for_stage()`
- `find_models_supporting_language()`
- `_parse_model_entry()`
- `ModelEntry` dataclass

Keep only engine-related methods.

#### 46.7.2: Remove models from `generate_catalog.py`

File: `scripts/generate_catalog.py`

Delete:

- `find_model_yamls()`
- `transform_model_to_entry()`
- Models section in output JSON
- All model-related imports and constants

Output becomes engines-only:

```json
{"engines": {...}, "engine_ids": {...}}
```

#### 46.7.3: Remove models from generated JSON

File: `dalston/orchestrator/generated_catalog.json`

Regenerate with engines-only - no `"models"` section.

#### 46.7.4: Remove `seed_from_catalog()` method

File: `dalston/gateway/services/model_registry.py`

Delete `seed_from_catalog()` entirely - replaced by `seed_from_yamls()`.

#### 46.7.5: Remove `/v1/models/registry` endpoint

File: `dalston/gateway/api/v1/models.py`

Delete:

- `list_registry()` handler
- `get_registry_model()` handler
- All `/registry` routes

The unified `GET /v1/models` replaces these.

#### 46.7.6: Remove CLI `model seed` command

File: `dalston/gateway/cli.py`

Delete `_seed_models()` command - seeding now happens automatically on startup.

#### 46.7.7: Update all catalog.get_model() callers

Search and replace ALL remaining `catalog.get_model()` calls with DB queries:

- `dalston/orchestrator/engine_selector.py` (primary)
- `dalston/gateway/api/v1/models.py` (if any remain)
- `dalston/gateway/api/v1/engines.py` (if any remain)

#### 46.7.8: Update tests

Remove/update tests that:

- Use `seed_from_catalog()`
- Query `/v1/models/registry`
- Mock `catalog.get_model()`
- Test model catalog loading

---

## Files Summary

### New Files

| File | Description |
|------|-------------|
| `alembic/versions/xxx_add_metadata_source.py` | Migration for metadata_source column |
| `dalston/gateway/services/model_yaml_loader.py` | Direct YAML loading module |

### Modified Files

| File | Change |
|------|--------|
| `dalston/db/models.py` | Add `metadata_source` column |
| `dalston/gateway/services/model_registry.py` | Add `seed_from_yamls()`, `update_model()`, remove `seed_from_catalog()` |
| `dalston/gateway/main.py` | Add YAML seeding in lifespan |
| `dalston/orchestrator/engine_selector.py` | Replace `catalog.get_model()` with DB query |
| `dalston/gateway/api/v1/models.py` | Add PATCH, unify list, remove `/registry` routes |
| `dalston/orchestrator/catalog.py` | Remove all model methods and `ModelEntry` |
| `scripts/generate_catalog.py` | Remove models section entirely |
| `dalston/gateway/cli.py` | Remove `model seed` command |

### Files to Regenerate

| File | Change |
|------|--------|
| `dalston/orchestrator/generated_catalog.json` | Regenerate without models section |

---

## Verification

### Migration

```bash
alembic upgrade head
# Verify column exists
psql dalston -c "SELECT metadata_source FROM models LIMIT 1;"
```

### Startup Seeding

```bash
# Start gateway
make dev

# Check logs
docker compose logs gateway | grep "model_registry_seeded"
# model_registry_seeded created=18 updated=0 preserved=0
```

### API

```bash
# List models (now from DB)
curl http://localhost:8000/v1/models | jq '.data | length'
# 18

# Update model metadata
curl -X PATCH http://localhost:8000/v1/models/nvidia%2Fparakeet-tdt-1.1b \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $API_KEY" \
  -d '{"name": "My Custom Name", "rtf_gpu": 0.0005}'

# Verify metadata_source changed
curl http://localhost:8000/v1/models/nvidia%2Fparakeet-tdt-1.1b | jq '.metadata_source'
# "user"
```

### Preserve User Edits

```bash
# Restart gateway
docker compose restart gateway

# Verify user-modified model NOT overwritten
curl http://localhost:8000/v1/models/nvidia%2Fparakeet-tdt-1.1b | jq '.name'
# "My Custom Name"
```

### Tests

```bash
make test
```

---

## Checkpoint Summary

- [ ] **46.1**: Migration adds `metadata_source` column
- [ ] **46.2**: YAML loader module created
- [ ] **46.3**: Gateway startup seeds from YAMLs
- [ ] **46.4**: Orchestrator uses DB for model lookups
- [ ] **46.5**: PATCH endpoint for user enrichment
- [ ] **46.6**: API endpoints unified (remove `/v1/models/registry`)
- [ ] **46.7**: Legacy code removed:
  - [ ] Model methods removed from `EngineCatalog`
  - [ ] `seed_from_catalog()` removed
  - [ ] `generate_catalog.py` models section removed
  - [ ] CLI `model seed` command removed
  - [ ] Tests updated

---

## Related Milestones

- **M40**: Model Registry & HuggingFace Integration (prerequisite)
- **M42**: Console Model Management (may need UI updates for metadata_source)
