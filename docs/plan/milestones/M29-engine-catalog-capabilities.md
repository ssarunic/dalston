# M29: Engine Catalog & Capabilities

| | |
|---|---|
| **Goal** | Engines declare capabilities; orchestrator validates jobs against them |
| **Duration** | 2-3 days |
| **Dependencies** | M28 complete (Batch Engine Registry) |
| **Deliverable** | Jobs fail fast with clear errors when no capable engine exists |
| **Status** | Complete |

## User Story

> *"As an operator, when I submit a job requesting Croatian transcription but no engine supports Croatian, I get an immediate clear error — not a silent queue timeout."*

> *"As a future scheduler, when all engines are off and a job arrives, I can check the catalog to decide which engine image to boot — without needing any running containers."*

## Context

M28 introduced a batch engine registry where engines heartbeat to Redis and the orchestrator fails fast (~300ms) when a required engine isn't running. But M28 only answers **"is the engine alive?"** — it doesn't answer **"can this engine do what the job needs?"**

This milestone adds two layers:

1. **Catalog** (static, deploy-time) — declares what engines *could* be started, their images, capabilities, and resource requirements. Available even when nothing is running.
2. **Rich registry** (runtime) — engines include their capabilities in heartbeats. The orchestrator validates job requirements against what's actually running.

### The three layers after M29

| Layer | Source | Answers | Available when |
|---|---|---|---|
| **Catalog** | Config file read at startup | "What could I start?" | Always |
| **Registry** | Redis heartbeats (M28 + enriched) | "What's running and capable?" | When engines are up |
| **Validation** | Orchestrator checks job vs registry | "Can a running engine do this job?" | At queue time |

---

## Steps

### 29.1: Define EngineCapabilities schema

Add to `engine_sdk/types.py` (or a new shared location if more appropriate):

```python
class EngineCapabilities(BaseModel):
    """What an engine can do. Published in heartbeats, declared in catalog."""
    engine_id: str                          # e.g. "parakeet", "faster-whisper"
    version: str                            # engine version
    stages: list[str]                       # ["transcribe"], ["diarize"], etc.
    languages: list[str] | None = None      # ISO 639-1 codes, None = all
    supports_word_timestamps: bool = False
    supports_streaming: bool = False
    model_variants: list[str] | None = None # e.g. ["large-v3", "medium", "tiny"]
    gpu_required: bool = False
    gpu_vram_mb: int | None = None          # estimated VRAM usage
```

Keep it flat. Don't over-model — we extend later.

### 29.2: Add get_capabilities() to base Engine class

In `engine_sdk/base.py`, add a method alongside the existing `health_check()`:

```python
def get_capabilities(self) -> EngineCapabilities:
    """Override in engine subclass to declare capabilities."""
    return EngineCapabilities(
        engine_id=self.engine_id,
        version="unknown",
        stages=[],
    )
```

### 29.3: Parakeet implements get_capabilities()

Start with Parakeet only — it's the engine that's registered and tested from M28. Hardcode the capabilities in Python (don't parse engine.yaml at runtime):

```python
def get_capabilities(self) -> EngineCapabilities:
    return EngineCapabilities(
        engine_id="parakeet",
        version="1.0.0",
        stages=["transcribe"],
        languages=["en", "de", "es", "fr", ...],  # Parakeet's actual language list
        supports_word_timestamps=True,
        supports_streaming=False,
        model_variants=["parakeet-tdt-1.1b"],
        gpu_required=True,
        gpu_vram_mb=4000,
    )
```

### 29.4: Include capabilities in heartbeat

Enrich the M28 heartbeat payload. Instead of just registering engine_id presence, the heartbeat now includes the full capabilities dict. Store in Redis so the orchestrator can read capabilities without making HTTP calls to engine containers.

Whatever Redis key structure M28 uses for the registry, extend it — don't create a parallel structure.

### 29.5: Define the engine catalog

Create a static config file (YAML or TOML, match whatever the project already uses for config) that the orchestrator reads at startup. This is the "menu" of what's deployable:

```yaml
# engine_catalog.yaml (example structure — adapt to project conventions)
engines:
  parakeet:
    image: dalston/parakeet:latest
    stages: [transcribe]
    languages: [en, de, es, fr, hr, sr, sl]
    supports_word_timestamps: true
    gpu_required: true
    gpu_vram_mb: 4000

  faster-whisper:
    image: dalston/faster-whisper:latest
    stages: [transcribe]
    languages: null  # all
    supports_word_timestamps: false
    model_variants: [large-v3, medium, tiny]
    gpu_required: true
    gpu_vram_mb: 5000

  pyannote-4.0:
    image: dalston/pyannote:latest
    stages: [diarize]
    languages: null
    gpu_required: true
    gpu_vram_mb: 1500

  audio-prepare:
    image: dalston/audio-prepare:latest
    stages: [prepare]
    gpu_required: false

  final-merger:
    image: dalston/final-merger:latest
    stages: [merge]
    gpu_required: false
```

The orchestrator loads this on startup. It replaces (or supplements) the hardcoded `DEFAULT_ENGINES` in `dag.py` as the source of truth for what engines exist.

### 29.6: Orchestrator validates job requirements

Extend the M28 fail-fast check with a second validation layer. When a job is submitted:

1. **Catalog check** — does any engine in the catalog handle this stage + requirements? If no: fail immediately with `"no engine in catalog supports language 'xx' for stage 'transcribe'"`. This catches configuration errors before anything runs.

2. **Registry check** (M28, already done) — is the required engine currently running?

3. **Capabilities check** (new) — does the running engine's registered capabilities match the job's requirements? If engine is registered but doesn't support the requested language: fail with `"engine 'parakeet' is running but does not support language 'xx'"`.

The error messages should be distinct so operators know whether to fix their catalog config, start an engine, or choose a different engine.

### 29.7: Tests

- **Unit**: EngineCapabilities schema validation, catalog loading, orchestrator validation logic against various job/capability combinations
- **Integration**: Engine registers with capabilities, job requesting unsupported language fails fast with clear error, job requesting supported language succeeds
- **Regression**: All M28 tests pass unchanged
- **Edge cases**: Engine registered without capabilities (backward compat with M28), catalog has engine but it's not running, multiple engines for same stage with different language coverage

---

## What NOT to do

- Don't build dynamic routing between multiple engines for the same stage — that's a future milestone
- Don't parse engine.yaml at runtime in engines — capabilities are declared in code via `get_capabilities()`
- Don't add capabilities to realtime engines — batch only for now
- Don't change the DAG builder or pipeline stage order — the catalog is a validation layer, not a routing replacement
- Don't build the auto-scaling / engine boot logic — the catalog enables it but the scheduler that acts on it is a separate milestone

---

## Checkpoint

✓ **EngineCapabilities** schema defined and shared between engine SDK and orchestrator
✓ **Parakeet** declares its capabilities via `get_capabilities()`
✓ **Heartbeats** include full capabilities payload
✓ **Engine catalog** loaded by orchestrator at startup
✓ **Job validation** checks catalog (does this config support it?) and registry (can a running engine handle it?)
✓ **Error messages** distinguish between "not in catalog", "not running", and "running but incapable"
✓ **M28 tests** pass unchanged

**Enables next**: Dynamic orchestrator routing (choosing between multiple registered engines), auto-scaling (catalog tells scheduler which image to boot for a given job)

---

## Files Changed

| File | Change |
|------|--------|
| `dalston/engine_sdk/types.py` | MODIFY — Add `EngineCapabilities` schema |
| `dalston/engine_sdk/base.py` | MODIFY — Add `get_capabilities()` method |
| `dalston/engine_sdk/registry.py` | MODIFY — Support capabilities in registration/heartbeat |
| `dalston/engine_sdk/runner.py` | MODIFY — Pass capabilities to registry |
| `dalston/engine_sdk/__init__.py` | MODIFY — Export `EngineCapabilities` |
| `dalston/orchestrator/catalog.py` | NEW — Engine catalog loader and validator |
| `dalston/orchestrator/engine_catalog.yaml` | NEW — Static engine catalog config |
| `dalston/orchestrator/registry.py` | MODIFY — Parse capabilities from Redis |
| `dalston/orchestrator/scheduler.py` | MODIFY — Add catalog/capability validation |
| `dalston/orchestrator/exceptions.py` | MODIFY — Add `EngineCapabilityError`, `CatalogValidationError` |
| `dalston/orchestrator/handlers.py` | MODIFY — Catch new exceptions |
| `engines/transcribe/parakeet/engine.py` | MODIFY — Implement `get_capabilities()` |
| `tests/unit/test_engine_capabilities.py` | NEW — 25 tests for M29 |
