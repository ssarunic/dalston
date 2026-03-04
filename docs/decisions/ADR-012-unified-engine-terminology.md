# ADR-012: Unified Engine Terminology (runtime, model, instance)

## Status

Proposed

## Context

Dalston has two processing modes—batch and real-time—that evolved independently. This led to inconsistent terminology for the same concepts:

| Concept | Batch Field | RT Field | Problem |
|---------|-------------|----------|---------|
| Inference framework | `engine_id` | `engine` + `runtime` | Three names for one thing |
| Model weights | `loaded_model` | `models_loaded[]` | Two names |
| Process identifier | `instance_id` | `worker_id` | Two names |

The RT side has both `engine` and `runtime` on `WorkerState`:

- `engine` is a legacy field (pre-M43) with loose categories like "parakeet" or "whisper"
- `runtime` was added in M43 to match batch's `engine_id`

This inconsistency causes problems:

1. **Observability confusion** — Trace attributes differ between batch and RT, complicating queries and dashboards
2. **Code duplication** — Similar logic written twice with different field names
3. **Onboarding friction** — New contributors must learn two vocabularies for the same concepts
4. **API inconsistency** — External consumers see different terminology depending on the endpoint

## Decision

Standardize on three canonical terms:

### 1. `runtime` — The inference framework

**Definition:** The software that loads model weights and runs inference. Examples: `faster-whisper`, `nemo-onnx`, `whisperx`.

**Why `runtime` over `engine`:**

- `engine_id` in batch registries already means the inference framework
- `engine.yaml` files have a `runtime` field that matches `engine_id`
- Model YAMLs use `runtime` to define which engine can load them
- The M43/M46 database schema standardized on `runtime`
- The legacy RT `engine` field ("parakeet", "whisper") is a loose category that doesn't map to anything actionable

**Mapping:**

- Batch: `engine_id``runtime`
- RT: deprecate `engine`, use `runtime` (already exists)

### 2. `model` — The user-facing model identifier

**Definition:** The namespaced model ID that users select or the system resolves. Examples: `nvidia/parakeet-tdt-1.1b`, `Systran/faster-whisper-large-v3`.

**Why `model` over `model_id` or `runtime_model_id`:**

- It's what users see and select in the UI
- It's what the model selector resolves to
- `runtime_model_id` (the string passed to the loader, e.g., HuggingFace repo path) is an internal implementation detail

**Internal detail (when needed):**

- `model.loader_id` or similar for the engine-specific loader string
- Belongs in engine debug logs, not traces or API responses

### 3. `instance` — The process identifier

**Definition:** Identifies which specific process handled the work. Used for cardinality analysis, debugging, and filtering.

**Why `instance` over `worker_id`/`instance_id`:**

- Both batch `instance_id` and RT `worker_id` serve the same purpose
- `instance` is shorter and mode-agnostic
- Standard term in observability (Prometheus, OpenTelemetry)

**Mapping:**

- Batch: `instance_id``instance`
- RT: `worker_id``instance`

## Consequences

### Trace Attribute Unification

Batch span:

```
engine.process
  dalston.runtime   = "faster-whisper"
  dalston.model     = "nvidia/parakeet-tdt-1.1b"
  dalston.instance  = "faster-whisper-a1b2c3d4"
  dalston.stage     = "transcribe"
  dalston.job_id    = "..."
  dalston.task_id   = "..."
```

RT span:

```
realtime.session
  dalston.runtime   = "faster-whisper"
  dalston.model     = "nvidia/parakeet-tdt-1.1b"
  dalston.instance  = "stt-rt-fw-1"
  dalston.session_id = "..."
```

Same attributes, same semantics. The span name and presence of `job_id` vs `session_id` distinguishes mode.

### Benefits

1. **Unified observability** — Same Grafana queries work for batch and RT
2. **Simpler mental model** — Three terms to learn, not six
3. **Cleaner APIs** — Consistent terminology in responses
4. **Easier refactoring** — Shared code can use the same field names

### Migration Path

**Phase 1: Trace attributes (non-breaking)**

- Map existing fields to canonical trace attribute names
- No data model changes required

**Phase 2: API responses (minor breaking)**

- Add canonical fields alongside existing ones
- Deprecate old field names with warnings

**Phase 3: Data model alignment (larger refactor)**

- Rename `BatchEngineInfo.engine_id``runtime` (or alias)
- Deprecate `WorkerInfo.engine` (already have `WorkerInfo.runtime`)
- Align model field names

### Costs

1. **Migration effort** — Phased approach minimizes disruption
2. **Backwards compatibility** — May need to support old field names temporarily
3. **Documentation updates** — All docs referencing old terms need updating

## Alternatives Considered

### Keep Separate Vocabularies

Status quo. Rejected because the cognitive overhead and observability fragmentation outweigh any benefit from mode-specific terminology.

### Use `engine` Instead of `runtime`

Would require deprecating `engine_id` in batch (widely used) rather than the legacy `engine` field in RT (narrowly used). More disruptive.

### Use `worker` Instead of `instance`

`worker` implies a long-running process, which fits RT but not batch task executors. `instance` is more generic.

## References

- M43: Real-time Registry Alignment — Added `runtime` to RT workers
- M46: Model Registry as Source of Truth — Standardized on `runtime` in DB schema
- [ADR-010: Engine Variant Structure](ADR-010-engine-variant-structure.md) — Related engine organization decisions
