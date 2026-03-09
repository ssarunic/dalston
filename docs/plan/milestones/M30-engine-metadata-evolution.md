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

Each `engines/{stage}/{name}/engine.yaml` serves as the single source of truth. At CI time it is validated against JSON Schema. At build time, a catalog is generated from all engine.yaml files and baked into the orchestrator image. At deploy time, the orchestrator loads the catalog to answer "what could I start?". At runtime, each engine loads its own engine.yaml and publishes capabilities via heartbeat, enabling the registry to answer "what's running?".

---

## Extended engine.yaml Schema

### New Sections

Three new optional sections are added to the existing `engine.yaml` format alongside existing fields (`id`, `stage`, `name`, `version`, `capabilities`, `container`, `input`, `config_schema`, `output_schema`). A `schema_version` field ("1.0" for baseline, "1.1" for extended) is also added. The new sections are `hf_compat` (HuggingFace ecosystem metadata), `hardware` (GPU/CPU/RAM requirements), and `performance` (RTF benchmarks, concurrency limits, warm-start latency). See any `engines/*/engine.yaml` file for a complete example.

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

CLI tool (`dalston/tools/validate_engine.py`) that validates engine.yaml files against the JSON Schema. Supports single-file validation and `--all` mode to validate every engine. Outputs engine ID, version, schema version, stage, and key capability/hardware summaries.

**Tests:** `tests/unit/test_validate_engine.py`

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

Build-time script (`scripts/generate_catalog.py`) that reads all `engines/*/engine.yaml` files and produces `dalston/orchestrator/generated_catalog.json`. The output contains generation timestamp, schema version, and a map of engine entries with their capabilities, hardware requirements, and performance data.

**Tests:** `tests/unit/test_generate_catalog.py`

---

### 30.7: Modify catalog.py to Load Generated Catalog

Updated `dalston/orchestrator/catalog.py` to load from `generated_catalog.json` (defaulting to the file adjacent to catalog.py) instead of the legacy `engine_catalog.yaml`.

**Tests:** `tests/unit/test_engine_capabilities.py` (modified)

---

### 30.8: Update Engine.get_capabilities() to Load from YAML

Modified `dalston/engine_sdk/base.py` so the base Engine class loads capabilities from `/etc/dalston/engine.yaml` (baked into container) at runtime, with a fallback to `./engine.yaml` for local development. This replaces hardcoded capability values.

---

### 30.9: Extend EngineCapabilities Model

Extended `EngineCapabilities` in `dalston/engine_sdk/types.py` with new fields: `supports_cpu`, `min_ram_gb`, `rtf_gpu`, `rtf_cpu`, and `max_concurrent_jobs`. These complement the existing fields (`engine_id`, `version`, `stages`, `languages`, `supports_word_timestamps`, `supports_streaming`, `gpu_required`, `gpu_vram_mb`).

---

### 30.10: Discovery API Endpoints

Two new REST endpoints in `dalston/gateway/api/v1/engines.py`:

- **GET /v1/engines** — Returns all engines with current status (`running`, `available`, or `unhealthy`) based on registry heartbeats vs. catalog entries.
- **GET /v1/capabilities** — Returns aggregate capabilities of running engines: supported languages, per-stage capabilities, max audio duration, and supported formats.

**Tests:** `tests/integration/test_engines_api.py`

---

### 30.11: Performance-Based Timeout Calculation

Updated `dalston/orchestrator/scheduler.py` to use `performance.rtf_gpu` from the catalog for timeout estimation. Timeout is calculated as `audio_duration * rtf * 3.0` (3x safety factor), with a minimum floor.

---

### 30.12: Improved Error Messages

Enhanced validation errors in `dalston/orchestrator/scheduler.py` and `dalston/orchestrator/exceptions.py` to include catalog context: what was required, what engines are available (with their capabilities and status), and actionable suggestions (e.g. "Start faster-whisper (supports all languages) or submit without word_timestamps").

---

### 30.13: Scaffold Command

CLI tool (`dalston/tools/scaffold_engine.py`) that generates a new engine skeleton under `engines/{stage}/{name}/` with `engine.yaml`, `Dockerfile`, `engine.py`, `requirements.txt`, and `README.md`. Templates are in `dalston/tools/templates/`.

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

- [ ] `python -m dalston.tools.validate_engine --all` passes for all 11 engines
- [ ] `scripts/generate_catalog.py` produces valid `generated_catalog.json` with all engine entries
- [ ] `GET /v1/engines` returns engine list with correct status (running/available/unhealthy)
- [ ] `GET /v1/capabilities` returns aggregate languages and per-stage capabilities
- [ ] Stopping an engine and submitting an unsupported request returns detailed error with suggestions

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


---

## Enables Next

- **Auto-scaling**: Hardware requirements enable instance type selection
- **HF ecosystem integration**: Pipeline tags enable model discovery
- **Client SDKs**: Discovery API enables capability negotiation
- **Contributor experience**: Scaffold + validation reduces onboarding friction

---

