# M30: Engine Metadata Evolution

|                  |                                                                                    |
| ---------------- | ---------------------------------------------------------------------------------- |
| **Goal**         | Single source of truth for engine metadata; eliminate catalog duplication          |
| **Duration**     | 8-10 days                                                                          |
| **Dependencies** | M28 (Batch Engine Registry), M29 (Engine Catalog & Capabilities)                   |
| **Deliverable**  | Extended engine.yaml schema, JSON Schema validation, discovery API, scaffold tools |
| **Status**       | Complete                                                                           |

## User Story

> *"As a contributor adding a new engine, I want to write one engine.yaml file with all metadata, and have the system automatically validate it, include it in the catalog, and expose its capabilities at runtime."*

> *"As an API client, I want to discover what capabilities a Dalston deployment supports before submitting jobs."*

---

## Context

M28 introduced the batch engine registry (heartbeats, fail-fast). M29 added capabilities to heartbeats and the engine catalog for validation. But two problems remain:

1. **Duplication**: Capability info exists in both `engines/*/engine.yaml` and `dalston/orchestrator/engine_catalog.yaml`. They can drift.

2. **Missing metadata**: The current `engine.yaml` lacks fields for HF ecosystem interop, hardware requirements, and performance characteristics.

This milestone makes `engine.yaml` the single source of truth by:

- Extending the schema with new sections
- Creating JSON Schema validation
- Generating the catalog from engine.yaml files at build time
- Loading capabilities from engine.yaml at runtime
- Exposing discovery APIs for clients

### Architecture After M30

```
engines/{stage}/{name}/engine.yaml     ─── single source of truth
        │
        ├─ CI: validated against JSON Schema
        │
        ├─ Build: catalog.json generated from all engine.yaml
        │       └── baked into orchestrator image
        │
        ├─ Deploy: orchestrator loads catalog.json
        │       └── answers "what could I start?"
        │
        └─ Runtime: engine loads its engine.yaml
                └── publishes via heartbeat
                └── registry answers "what's running?"
```

---

## Extended engine.yaml Schema

### New Sections

Add three new sections to the existing `engine.yaml` format:

```yaml
# === EXISTING FIELDS (unchanged) ===
schema_version: "1.1"                    # NEW: version for migration
id: faster-whisper
stage: transcribe
name: Faster Whisper Large V3
version: "1.2.0"
description: |
  CTranslate2-optimized Whisper implementation.

capabilities:
  languages: [all]
  max_audio_duration: 7200
  streaming: false
  word_timestamps: true

container:
  gpu: optional
  memory: "8Gi"
  model_cache: /models

input:
  audio_formats: [wav, flac, mp3]
  sample_rate: 16000
  channels: 1

config_schema: { ... }
output_schema: { ... }

# === NEW: HF-compatible metadata ===
hf_compat:
  pipeline_tag: automatic-speech-recognition
  library_name: ctranslate2
  license: apache-2.0

# === NEW: Hardware requirements ===
hardware:
  min_vram_gb: 4
  recommended_gpu: [a10g, t4]
  supports_cpu: true
  min_ram_gb: 8

# === NEW: Performance characteristics ===
performance:
  rtf_gpu: 0.05                          # real-time factor on GPU
  rtf_cpu: 0.8                           # real-time factor on CPU
  max_concurrent_jobs: 4
  warm_start_latency_ms: 50
```

### Field Definitions

#### hf_compat (optional)

| Field | Type | Description |
|-------|------|-------------|
| `pipeline_tag` | string | HuggingFace task taxonomy |
| `library_name` | string | Underlying ML framework |
| `license` | string | SPDX license identifier |

**pipeline_tag values:**

- HF standard: `automatic-speech-recognition`, `speaker-diarization`, `voice-activity-detection`, `audio-classification`
- Dalston extensions: `dalston:audio-preparation`, `dalston:merge`, `dalston:pii-redaction`, `dalston:audio-redaction`

#### hardware (optional)

| Field | Type | Description |
|-------|------|-------------|
| `min_vram_gb` | int | Minimum GPU VRAM in GB |
| `recommended_gpu` | list | GPU types: `a10g`, `t4`, `l4`, `a100`, `h100` |
| `supports_cpu` | bool | Whether CPU inference works |
| `min_ram_gb` | int | Minimum system RAM in GB |

#### performance (optional)

| Field | Type | Description |
|-------|------|-------------|
| `rtf_gpu` | float | Real-time factor on GPU (0.05 = 20x faster than real-time) |
| `rtf_cpu` | float | Real-time factor on CPU, null if unsupported |
| `max_concurrent_jobs` | int | Concurrent job limit |
| `warm_start_latency_ms` | int | Latency after model loaded |

---

## Steps

### 30.1: Add schema_version to Existing Files

Add `schema_version: "1.0"` to every existing `engine.yaml` file. This establishes a baseline for migration.

**Files changed:**

- `engines/prepare/audio-prepare/engine.yaml`
- `engines/transcribe/faster-whisper/engine.yaml`
- `engines/transcribe/parakeet/engine.yaml`
- `engines/align/whisperx-align/engine.yaml`
- `engines/diarize/pyannote-3.1/engine.yaml`
- `engines/diarize/pyannote-4.0/engine.yaml`
- `engines/detect/pii-presidio/engine.yaml`
- `engines/redact/audio-redactor/engine.yaml`
- `engines/merge/final-merger/engine.yaml`
- `engines/realtime/whisper-streaming/engine.yaml`
- `engines/realtime/parakeet-streaming/engine.yaml`

---

### 30.2: Create JSON Schema

Create `dalston/schemas/engine.schema.json` that validates engine.yaml files.

**Schema requirements:**

- Require all existing fields
- Make new sections (`hf_compat`, `hardware`, `performance`) optional
- Enum constraints for `stage`, `pipeline_tag`, `recommended_gpu`
- Accept both schema_version "1.0" and "1.1"

**Files:**

- NEW: `dalston/schemas/engine.schema.json`

---

### 30.3: Build Validator CLI

Create a CLI tool that validates engine.yaml against the JSON Schema.

```bash
# Validate single file
python -m dalston.tools.validate_engine engines/transcribe/faster-whisper/engine.yaml

# Validate all engines
python -m dalston.tools.validate_engine --all

# Output
✓ faster-whisper v1.2.0 (schema 1.1)
  Stage: transcribe
  Languages: all
  Hardware: min 4GB VRAM, supports CPU
  Performance: RTF 0.05 (GPU)
```

**Files:**

- NEW: `dalston/tools/validate_engine.py`
- NEW: `dalston/tools/__init__.py`

**Tests:**

- NEW: `tests/unit/test_validate_engine.py`

---

### 30.4: Add CI Validation

Add GitHub Actions step to validate all engine.yaml files on PR.

**Files:**

- MODIFY: `.github/workflows/ci.yml` (or equivalent)

---

### 30.5: Update engine.yaml Files with New Sections

Add `hf_compat`, `hardware`, and `performance` sections to all engine.yaml files. Update `schema_version` to "1.1".

This requires benchmarking each engine for RTF values and documenting actual hardware requirements.

**Files:**

- MODIFY: All `engines/*/engine.yaml` files

---

### 30.6: Catalog Generation Script

Create a build-time script that generates catalog.json from engine.yaml files.

```bash
python scripts/generate_catalog.py \
  --engines-dir engines/ \
  --output dalston/orchestrator/generated_catalog.json
```

**Output format:**

```json
{
  "generated_at": "2026-02-16T10:30:00Z",
  "schema_version": "1.1",
  "engines": {
    "faster-whisper": {
      "id": "faster-whisper",
      "stage": "transcribe",
      "version": "1.2.0",
      "image": "dalston/stt-batch-transcribe-whisper:1.2.0",
      "capabilities": { ... },
      "hardware": { ... },
      "performance": { ... }
    }
  }
}
```

**Files:**

- NEW: `scripts/generate_catalog.py`

**Tests:**

- NEW: `tests/unit/test_generate_catalog.py`

---

### 30.7: Modify catalog.py to Load Generated Catalog

Update `dalston/orchestrator/catalog.py` to load from `generated_catalog.json` instead of `engine_catalog.yaml`.

```python
class EngineCatalog:
    @classmethod
    def load(cls, path: Path | None = None) -> EngineCatalog:
        if path is None:
            path = Path(__file__).parent / "generated_catalog.json"

        with open(path) as f:
            data = json.load(f)

        # Parse entries...
```

**Files:**

- MODIFY: `dalston/orchestrator/catalog.py`

**Tests:**

- MODIFY: `tests/unit/test_engine_capabilities.py`

---

### 30.8: Update Engine.get_capabilities() to Load from YAML

Modify base Engine class to load capabilities from `/etc/dalston/engine.yaml` (baked into container) instead of hardcoded values.

```python
class Engine(ABC):
    def get_capabilities(self) -> EngineCapabilities:
        card = self._load_engine_yaml()
        return EngineCapabilities(
            engine_id=card["id"],
            version=card["version"],
            stages=[card["stage"]],
            languages=card["capabilities"].get("languages"),
            supports_word_timestamps=card["capabilities"].get("word_timestamps", False),
            # ... etc
        )

    def _load_engine_yaml(self) -> dict:
        path = Path("/etc/dalston/engine.yaml")
        if not path.exists():
            # Fallback for local dev
            path = Path("engine.yaml")
        with open(path) as f:
            return yaml.safe_load(f)
```

**Files:**

- MODIFY: `dalston/engine_sdk/base.py`

---

### 30.9: Extend EngineCapabilities Model

Add fields to `EngineCapabilities` for hardware and performance data.

```python
class EngineCapabilities(BaseModel):
    # Existing fields...
    engine_id: str
    version: str
    stages: list[str]
    languages: list[str] | None = None
    supports_word_timestamps: bool = False
    supports_streaming: bool = False
    gpu_required: bool = False
    gpu_vram_mb: int | None = None

    # NEW fields
    supports_cpu: bool = False
    min_ram_gb: int | None = None
    rtf_gpu: float | None = None
    rtf_cpu: float | None = None
    max_concurrent_jobs: int | None = None
```

**Files:**

- MODIFY: `dalston/engine_sdk/types.py`

---

### 30.10: Discovery API Endpoints

Add REST endpoints for clients to discover capabilities.

#### GET /v1/engines

Returns all engines with current status.

```json
{
  "engines": [
    {
      "id": "faster-whisper",
      "name": "Faster Whisper Large V3",
      "stage": "transcribe",
      "version": "1.2.0",
      "status": "running",
      "capabilities": { ... }
    }
  ]
}
```

**Status values:**

- `running` — In registry with valid heartbeat
- `available` — In catalog but not running
- `unhealthy` — In registry but heartbeat expired

#### GET /v1/capabilities

Returns aggregate capabilities of running engines.

```json
{
  "languages": ["en", "es", "fr"],
  "stages": {
    "transcribe": { "languages": ["en", "es", "fr"], "word_timestamps": true },
    "diarize": { "languages": null }
  },
  "max_audio_duration_s": 7200,
  "supported_formats": ["wav", "flac", "mp3"]
}
```

**Files:**

- NEW: `dalston/gateway/api/v1/engines.py`
- MODIFY: `dalston/gateway/api/v1/router.py`

**Tests:**

- NEW: `tests/integration/test_engines_api.py`

---

### 30.11: Performance-Based Timeout Calculation

Use `performance.rtf_gpu` in the scheduler for timeout estimation.

```python
def calculate_timeout(audio_duration_s: float, engine: CatalogEntry) -> float:
    rtf = engine.performance.get("rtf_gpu") or 1.0
    estimated = audio_duration_s * rtf
    return max(estimated * 3.0, MIN_TIMEOUT_S)  # 3x safety factor
```

**Files:**

- MODIFY: `dalston/orchestrator/scheduler.py`

---

### 30.12: Improved Error Messages

Enhance validation errors to include catalog context and suggestions.

```json
{
  "error": "catalog_validation_error",
  "message": "No engine supports language 'hr' with word_timestamps for stage 'transcribe'",
  "details": {
    "required": { "stage": "transcribe", "language": "hr", "word_timestamps": true },
    "available_engines": [
      { "id": "parakeet", "languages": ["en"], "word_timestamps": true, "status": "running" },
      { "id": "faster-whisper", "languages": null, "word_timestamps": false, "status": "available" }
    ],
    "suggestion": "Start faster-whisper (supports all languages) or submit without word_timestamps"
  }
}
```

**Files:**

- MODIFY: `dalston/orchestrator/scheduler.py`
- MODIFY: `dalston/orchestrator/exceptions.py`

---

### 30.13: Scaffold Command

Create a CLI to scaffold new engines.

```bash
python -m dalston.tools.scaffold_engine my-engine --stage transcribe

# Creates:
# engines/transcribe/my-engine/
# ├── engine.yaml          (template with all fields)
# ├── Dockerfile
# ├── engine.py
# ├── requirements.txt
# └── README.md
```

**Files:**

- NEW: `dalston/tools/scaffold_engine.py`
- NEW: `dalston/tools/templates/` (template files)

---

### 30.14: Deprecate engine_catalog.yaml

After catalog.py reads from generated_catalog.json:

1. Add deprecation warning if engine_catalog.yaml is accessed directly
2. Document migration in this milestone
3. Delete in subsequent release

**Files:**

- DELETE (after migration): `dalston/orchestrator/engine_catalog.yaml`

---

### 30.15: Update Documentation

Update `docs/specs/batch/ENGINES.md` to document the extended schema.

**Files:**

- MODIFY: `docs/specs/batch/ENGINES.md`

---

## What NOT to Do

- Don't implement `--from-hf` scaffolding — defer until ecosystem need
- Don't build runtime profile system (Phase 5 of original proposal) — defer until customers request custom models
- Don't add capabilities to realtime engines — batch only for now
- Don't change DAG builder or pipeline stages — this is metadata evolution, not routing changes

---

## Verification

```bash
# 1. Validate all engine.yaml files
python -m dalston.tools.validate_engine --all
# All 11 engines valid

# 2. Generate catalog
python scripts/generate_catalog.py --engines-dir engines/ --output test_catalog.json
cat test_catalog.json | jq '.engines | keys'
# ["audio-prepare", "faster-whisper", "final-merger", ...]

# 3. Start system and check discovery API
docker compose up -d
curl http://localhost:8000/v1/engines | jq '.[0]'
# { "id": "faster-whisper", "status": "running", ... }

curl http://localhost:8000/v1/capabilities | jq '.languages'
# ["en", "es", "fr", ...]

# 4. Verify error messages
docker compose stop stt-batch-transcribe-whisper-cpu
curl -X POST http://localhost:8000/v1/audio/transcriptions \
  -H "Authorization: Bearer dk_test" \
  -F "file=@test.mp3" \
  -F "language=hr" | jq '.error'
# Detailed error with available engines and suggestions
```

---

## Checkpoint

- [x] `schema_version: "1.0"` added to all existing engine.yaml files
- [x] JSON Schema created at `dalston/schemas/engine.schema.json`
- [x] Validator CLI works: `python -m dalston.tools.validate_engine --all`
- [x] CI validates engine.yaml on PR (pre-commit hook)
- [x] All engine.yaml files updated with new sections (schema 1.1)
- [x] Catalog generation script works (`scripts/generate_catalog.py`)
- [x] catalog.py loads from generated_catalog.json
- [x] Engine.get_capabilities() loads from engine.yaml
- [x] EngineCapabilities model extended with new fields
- [x] GET /v1/engines endpoint returns engine list with status
- [x] GET /v1/capabilities endpoint returns aggregate capabilities
- [x] Performance-based timeout calculation works
- [x] Error messages include catalog context and suggestions
- [x] Scaffold command generates new engine skeleton (`dalston/tools/scaffold_engine.py`)
- [x] engine_catalog.yaml deleted
- [x] ENGINES.md updated with new schema

---

## Files Changed

| File | Change |
|------|--------|
| `dalston/schemas/engine.schema.json` | NEW |
| `dalston/tools/__init__.py` | NEW |
| `dalston/tools/validate_engine.py` | NEW |
| `dalston/tools/scaffold_engine.py` | NEW |
| `dalston/tools/templates/` | NEW |
| `scripts/generate_catalog.py` | NEW |
| `dalston/orchestrator/generated_catalog.json` | NEW (generated) |
| `dalston/orchestrator/catalog.py` | MODIFY |
| `dalston/orchestrator/scheduler.py` | MODIFY |
| `dalston/orchestrator/exceptions.py` | MODIFY |
| `dalston/engine_sdk/base.py` | MODIFY |
| `dalston/engine_sdk/types.py` | MODIFY |
| `dalston/gateway/api/v1/engines.py` | NEW |
| `dalston/gateway/api/v1/router.py` | MODIFY |
| `engines/*/engine.yaml` | MODIFY (all 11 files) |
| `dalston/orchestrator/engine_catalog.yaml` | DELETE |
| `docs/specs/batch/ENGINES.md` | MODIFY |
| `.github/workflows/ci.yml` | MODIFY |
| `tests/unit/test_validate_engine.py` | NEW |
| `tests/unit/test_generate_catalog.py` | NEW |
| `tests/integration/test_engines_api.py` | NEW |

---

## Implementation Order

| Step | Scope | Effort |
|------|-------|--------|
| 30.1 | Add schema_version to existing files | 0.5 day |
| 30.2 | Create JSON Schema | 1 day |
| 30.3 | Build validator CLI | 0.5 day |
| 30.4 | Add CI validation | 0.5 day |
| 30.5 | Update engine.yaml files with new sections | 1 day |
| 30.6 | Catalog generation script | 1 day |
| 30.7 | Modify catalog.py | 0.5 day |
| 30.8 | Update Engine.get_capabilities() | 0.5 day |
| 30.9 | Extend EngineCapabilities model | 0.5 day |
| 30.10 | Discovery API endpoints | 1 day |
| 30.11 | Performance-based timeout | 0.5 day |
| 30.12 | Improved error messages | 0.5 day |
| 30.13 | Scaffold command | 1 day |
| 30.14-15 | Cleanup and docs | 0.5 day |

**Total: ~9 days**

---

## Enables Next

- **Auto-scaling**: Hardware requirements enable instance type selection
- **HF ecosystem integration**: Pipeline tags enable model discovery
- **Client SDKs**: Discovery API enables capability negotiation
- **Contributor experience**: Scaffold + validation reduces onboarding friction

---

## Implementation Summary

**Completed: February 2026**

M30 was implemented in 4 phases:

### Phase 1: Schema Validation

- Created `dalston/schemas/engine.schema.json` with full validation
- Built `dalston/tools/validate_engine.py` CLI tool
- Added `schema_version: "1.0"` to all engine.yaml files

### Phase 2: Extended Metadata & Catalog Generation

- Extended all engine.yaml files with `hf_compat`, `hardware`, and `performance` sections
- Created `scripts/generate_catalog.py` to build catalog from engine.yaml files
- Generated `dalston/orchestrator/generated_catalog.json`

### Phase 3: Discovery API & Error Handling

- Added `GET /v1/engines` endpoint with engine status (running/available/unhealthy)
- Added `GET /v1/capabilities` endpoint for aggregate capability discovery
- Enhanced error messages with catalog context and suggestions

### Phase 4: Runtime & Tooling

- Modified `Engine.get_capabilities()` to load from engine.yaml at runtime
- Created `dalston/tools/scaffold_engine.py` for new engine scaffolding
- Deleted legacy `dalston/orchestrator/engine_catalog.yaml`
- Extended `EngineCapabilities` model with hardware/performance fields

### Key Files Created

- `dalston/schemas/engine.schema.json`
- `dalston/tools/validate_engine.py`
- `dalston/tools/scaffold_engine.py`
- `dalston/gateway/api/v1/engines.py`
- `scripts/generate_catalog.py`
- `dalston/orchestrator/generated_catalog.json`

### Stage Names Updated

During implementation, stage names were consolidated:

- `detect` → `pii_detect` (PII detection stage)
- `redact` → `audio_redact` (audio redaction stage)
