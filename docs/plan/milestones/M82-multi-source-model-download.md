# M82: Multi-Source Model Download

|                    |                                                              |
| ------------------ | ------------------------------------------------------------ |
| **Goal**           | Engines download models directly from HuggingFace Hub (or NGC) when running standalone, without requiring S3/gateway infrastructure |
| **Duration**       | 3–5 days                                                     |
| **Dependencies**   | M79 (Leaf Engine HTTP API), M39 (Model Cache TTL)            |
| **Deliverable**    | `ModelStorage` abstraction with S3/HF/NGC backends; `DALSTON_MODEL_SOURCE` env var; short model name aliases removed from engine managers |
| **Status**         | Not Started                                                  |

## User Story

> *"As an operator running a single engine container without the full Dalston stack, I want the engine to download its model directly from HuggingFace Hub on first request, so that I don't need S3, gateway, or orchestrator infrastructure."*

---

## Outcomes

| Scenario | Current | After M82 |
| -------- | ------- | ---------- |
| Single engine, no S3 | Engine fails to load model — `DALSTON_S3_BUCKET` required | Engine downloads from HF Hub using `HF_TOKEN`, caches locally |
| Full stack with S3 | Gateway pulls from HF → S3 → engine pulls from S3 | Unchanged (S3 remains default) |
| Engine with `DALSTON_MODEL_SOURCE=auto` | N/A | Tries local cache → S3 → HF Hub, uses first that works |
| `POST /v1/transcribe` with `model=Systran/faster-whisper-base` | Short alias `base` required in some managers | Full HF repo ID accepted everywhere; short aliases removed |

---

## Architecture

```
                        ModelStorage.ensure_local(model_id)
                                    │
                          ┌─────────┴─────────┐
                          │  Check local cache │
                          └─────────┬─────────┘
                                    │ miss
                    ┌───────────────┼───────────────┐
                    │               │               │
              source=s3       source=auto      source=hf
                    │               │               │
                    ▼          ┌────┴────┐          │
              S3 download      │ Try S3  │          │
                               └────┬────┘          │
                                    │ fail          │
                               ┌────┴────┐          │
                               │ Try HF  │◄─────────┘
                               └────┬────┘
                                    │ fail
                               ┌────┴────┐
                               │ Try NGC │  (if NGC_API_KEY set)
                               └────┬────┘
                                    │
                                    ▼
                            Local cache path
                       (native framework layout)
```

---

## Steps

### 82.1: `ModelStorage` Abstraction

Replace direct `S3ModelStorage` usage in engine managers with a `ModelStorage` protocol that dispatches to the configured backend.

**Files modified:**

- `dalston/engine_sdk/model_storage.py` — add `ModelStorage` protocol and `MultiSourceModelStorage` implementation
- `dalston/engine_sdk/model_paths.py` — no changes (HF/NeMo cache paths already defined)

**Deliverables:**

```python
class ModelSource(str, Enum):
    S3 = "s3"
    HF = "hf"
    NGC = "ngc"
    AUTO = "auto"


class ModelStorage(Protocol):
    """Protocol for model storage backends."""

    def ensure_local(self, model_id: str) -> Path:
        """Ensure model is available locally. Returns path to model dir."""
        ...

    def is_cached_locally(self, model_id: str) -> bool: ...


class HFModelStorage:
    """Downloads models from HuggingFace Hub using native cache layout.

    Uses huggingface_hub.snapshot_download() which handles:
    - Content-addressed blob storage with symlink deduplication
    - Resumable downloads
    - Partial download recovery
    """

    def __init__(self, token: str | None = None) -> None:
        self.token = token or os.environ.get("HF_TOKEN")

    def ensure_local(self, model_id: str) -> Path:
        from huggingface_hub import snapshot_download
        # Downloads to HF_HOME native cache, returns snapshot path
        return Path(snapshot_download(model_id, token=self.token))

    def is_cached_locally(self, model_id: str) -> bool:
        return is_model_cached(model_id, framework="huggingface")


class MultiSourceModelStorage:
    """Tries backends in order based on DALSTON_MODEL_SOURCE config.

    Resolution order for 'auto':
      1. Local cache (any framework) → use immediately
      2. S3 (if DALSTON_S3_BUCKET set) → download + cache
      3. HF Hub (if HF_TOKEN set) → snapshot_download to native cache
      4. NGC (if NGC_API_KEY set) → ngc download to native cache
      5. Raise ModelNotFoundError
    """

    @classmethod
    def from_env(cls) -> MultiSourceModelStorage:
        source = os.environ.get("DALSTON_MODEL_SOURCE", "s3")
        ...
```

The `HFModelStorage` backend delegates entirely to `huggingface_hub.snapshot_download()`, which manages its own cache at `HF_HOME`. No `.complete` markers, no dalston-specific cache layout — HF's native deduplication and symlinks are used as-is. Framework-specific managers already know how to load from HF cache paths via `model_paths.get_hf_model_path()`.

---

### 82.2: NGC Backend (Stub)

Add `NGCModelStorage` as a thin wrapper around NGC CLI / API for NVIDIA model downloads.

**Files modified:**

- `dalston/engine_sdk/model_storage.py` — add `NGCModelStorage` class

**Deliverables:**

```python
class NGCModelStorage:
    """Downloads models from NVIDIA NGC registry.

    Requires NGC_API_KEY environment variable.
    Uses nemo cache layout at model_paths.NEMO_CACHE.
    """

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or os.environ.get("NGC_API_KEY")

    def ensure_local(self, model_id: str) -> Path:
        # NGC models use org/name format, e.g. "nvidia/parakeet-tdt-0.6b-v2"
        # Download via NGC Python API or nemo.collections auto-download
        ...
```

Initial implementation can be a stub that raises `NotImplementedError` with a clear message. The NGC download path can be filled in when we add NeMo engines that need it.

---

### 82.3: Wire `MultiSourceModelStorage` into Engine Managers

Replace direct `S3ModelStorage` construction in framework-specific managers with `MultiSourceModelStorage.from_env()`.

**Files modified:**

- `dalston/engine_sdk/managers/faster_whisper.py` — use `MultiSourceModelStorage` instead of `S3ModelStorage`; remove `SUPPORTED_MODELS` short-name set
- `dalston/engine_sdk/managers/hf_transformers.py` — use `MultiSourceModelStorage`
- `dalston/engine_sdk/managers/nemo.py` — use `MultiSourceModelStorage`
- `dalston/engine_sdk/managers/onnx.py` — use `MultiSourceModelStorage` if applicable
- `dalston/engine_sdk/model_manager.py` — accept `ModelStorage` in constructor instead of optional `S3ModelStorage`

**Deliverables:**

Each manager's `_load_model(model_id)` method currently does:
1. Check if `S3ModelStorage` is configured → download from S3
2. Else, try framework-native download (some managers have this, some don't)

After this step:
1. Call `self.storage.ensure_local(model_id)` — `MultiSourceModelStorage` handles the backend selection
2. Load model from the returned local path

The `SUPPORTED_MODELS` frozenset in `FasterWhisperModelManager` (which maps short names like `"base"`, `"large-v3"`) is removed. All model references use full HF repo IDs (e.g., `Systran/faster-whisper-base`). The `loaded_model_id` field in `TranscriptionRequest` and the `model` HTTP form parameter both accept full repo IDs only.

---

### 82.4: Preload Support for All Sources

Ensure `DALSTON_MODEL_PRELOAD` works with HF and NGC sources, not just S3.

**Files modified:**

- `dalston/engine_sdk/model_manager.py` — preload calls `storage.ensure_local()` before `_load_model()`

**Deliverables:**

The existing preload logic in `ModelManager.__init__` already calls `self.acquire(preload_id)`. Since `acquire` → `_load_model` → `storage.ensure_local`, this should work automatically once 82.3 is done. This step is primarily verification and testing:

- Confirm preload downloads from HF when `DALSTON_MODEL_SOURCE=hf`
- Confirm preload downloads from S3 when `DALSTON_MODEL_SOURCE=s3`
- Confirm preload tries both in order when `DALSTON_MODEL_SOURCE=auto`
- Add structured log events for preload source (`model_preloaded`, `source=hf|s3|ngc`)

---

### 82.5: Update Gateway Model Registry Compatibility

Ensure the gateway's model pull flow (HF → S3) still works and that full repo IDs are used throughout.

**Files modified:**

- `dalston/gateway/services/model_registry.py` — use `source` field as canonical model ID (stop relying on short aliases)
- `dalston/common/pipeline_types.py` — update `loaded_model_id` field description to specify full HF repo ID format

**Deliverables:**

No behavioral change to the gateway's pull flow. The gateway still downloads from HF and uploads to S3 for fleet distribution. The only change is that `loaded_model_id` consistently uses full repo IDs everywhere — the gateway no longer maps short aliases when dispatching to engines.

---

## Non-Goals

- **Model preload UX improvements** — preloading works via existing `DALSTON_MODEL_PRELOAD` env var; a richer preload mechanism (progress reporting, multiple models) is a separate milestone
- **Automatic framework detection from HF model card** — the gateway's `hf_resolver.py` handles routing to the correct engine type; engines themselves don't need to detect framework from model metadata
- **NGC full implementation** — 82.2 is a stub; full NGC download support ships when NeMo engine work requires it
- **Model garbage collection** — HF Hub's cache grows without bound; disk management is a separate concern (related to M39 Model Cache TTL)
- **Gateway-less fleet distribution** — engines downloading from HF directly doesn't replace S3 for multi-engine fleets where you want a single controlled pull

---

## Deployment

No migration required. The default value of `DALSTON_MODEL_SOURCE` is `s3`, preserving current behavior. Operators opt in to `auto` or `hf` explicitly.

**Breaking change:** Short model aliases (e.g., `base`, `large-v3`) are removed. Any existing `DALSTON_MODEL_PRELOAD` or job configs using short names must be updated to full repo IDs. This is acceptable because:
- The model registry DB already stores `source` (full repo ID)
- Short names were only used internally in engine managers
- No public API contract depends on short names

---

## Verification

```bash
# Start a single faster-whisper engine with HF source (no S3, no gateway)
export DALSTON_MODEL_SOURCE=hf
export HF_TOKEN=hf_xxxxxxxxxxxxxxxxxxxxx
export DALSTON_MODEL_PRELOAD=Systran/faster-whisper-base

# Run engine standalone
python -m dalston.engines.stt_transcribe.faster_whisper.engine

# Verify model was downloaded from HF
curl -s http://localhost:9100/health | jq '.models'

# Submit transcription with full model ID
curl -s -X POST http://localhost:9100/v1/transcribe \
  -F "file=@test.wav" \
  -F "model=Systran/faster-whisper-base" | jq '.text'

# Verify with auto mode and S3 configured (should prefer S3)
export DALSTON_MODEL_SOURCE=auto
export DALSTON_S3_BUCKET=dalston-artifacts
# Restart engine, check logs for "downloading_from=s3"
```

---

## Checkpoint

- [ ] `HFModelStorage` downloads models via `snapshot_download()` to native HF cache
- [ ] `NGCModelStorage` stub exists with clear `NotImplementedError`
- [ ] `MultiSourceModelStorage` resolves backends based on `DALSTON_MODEL_SOURCE` env var
- [ ] `DALSTON_MODEL_SOURCE=s3` (default) preserves current behavior exactly
- [ ] `DALSTON_MODEL_SOURCE=hf` downloads from HF Hub without S3 configured
- [ ] `DALSTON_MODEL_SOURCE=auto` falls back from S3 → HF → NGC
- [ ] Short model aliases removed from `FasterWhisperModelManager.SUPPORTED_MODELS`
- [ ] All engine managers use `MultiSourceModelStorage` instead of direct `S3ModelStorage`
- [ ] `DALSTON_MODEL_PRELOAD` works with all source modes
- [ ] Gateway model pull flow unchanged (HF → S3 → engine)
