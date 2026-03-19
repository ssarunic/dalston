# M83: Disk Cache Eviction

|                    |                                                              |
| ------------------ | ------------------------------------------------------------ |
| **Goal**           | Automatically evict stale model files from the local disk cache to prevent unbounded disk growth |
| **Duration**       | 1–2 days                                                     |
| **Dependencies**   | M39 (Unified Model Cache), M82 (Multi-Source Model Download) |
| **Deliverable**    | `DiskCacheEvictor` class, `.last_accessed` marker tracking, `DALSTON_MODEL_CACHE_MAX_GB` / `DALSTON_MODEL_CACHE_TTL_HOURS` env vars |
| **Status**         | Done                                                         |

## User Story

> *"As an operator running engines on instances with limited disk, I want stale model files to be cleaned up automatically, so that disk usage stays bounded without manual intervention."*

---

## Outcomes

| Scenario | Current | After M83 |
| -------- | ------- | ---------- |
| Engine downloads 5 models over a week, disk fills up | Models stay on disk forever; operator must manually delete or nuke the volume | Oldest models evicted when disk budget exceeded or TTL expired |
| Engine restarts after eviction | Model re-downloads from S3/HF on next request (cache miss) | Same — cache miss triggers fresh download, which is the intended behavior |
| Model is loaded in memory and idle on disk past TTL | Disk file deleted, in-memory model crashes on next internal read | Disk evictor skips models currently loaded in `ModelManager._models` |

---

## Architecture

```
                    ensure_local(model_id)
                            │
                            ▼
                ┌───────────────────────┐
                │  MultiSourceModelStorage │
                │  (S3 / HF / NGC / auto)  │
                └───────────┬───────────┘
                            │ download
                            ▼
                    /models/s3-cache/
                    /models/huggingface/hub/
                            │
                            │  touch .last_accessed
                            ▼
              ┌──────────────────────────┐
              │    DiskCacheEvictor       │
              │  (background thread)      │
              │                           │
              │  every 10 min:            │
              │    scan /models/          │
              │    skip in-memory models  │
              │    evict if:              │
              │      age > TTL    OR      │
              │      total > MAX_GB (LRU) │
              └──────────────────────────┘
```

---

## Steps

### 83.1: Access Tracking

Record last access time whenever a model is served from cache.

**Files modified:**

- `dalston/engine_sdk/model_storage.py` — touch `.last_accessed` marker in `S3ModelStorage.ensure_local()` and `HFModelStorage.ensure_local()` after returning a cached path

**Deliverables:**

Each `ensure_local()` call touches a `.last_accessed` file in the model directory with the current Unix timestamp as content. This works for both cache hits and fresh downloads.

```python
def _touch_access_marker(path: Path) -> None:
    """Record last access time for disk cache eviction."""
    marker = path / ".last_accessed"
    marker.write_text(str(time.time()))
```

For S3 cache, the model dir is `/models/s3-cache/Systran--faster-whisper-base/`. For HF cache, the snapshot dir returned by `snapshot_download()` — e.g., `/models/huggingface/hub/models--Systran--faster-whisper-base/snapshots/abc123/`. The marker goes in the top-level model dir (not the snapshot), so for HF that means the `models--Systran--faster-whisper-base/` directory.

---

### 83.2: DiskCacheEvictor

Background thread that scans the model cache and evicts stale or over-budget entries.

**Files modified:**

- `dalston/engine_sdk/disk_cache.py` *(new)* — `DiskCacheEvictor` class

**Deliverables:**

```python
class DiskCacheEvictor:
    """Background evictor for on-disk model cache.

    Runs a periodic scan of the model cache directory and removes
    model directories that are:
    - Older than max_age_hours since last access (TTL eviction)
    - Over the max_gb disk budget (LRU eviction — oldest first)

    Models currently loaded in memory (via ModelManager) are never
    evicted from disk.

    Environment variables:
        DALSTON_MODEL_CACHE_MAX_GB: Max disk usage in GB (default: 0 = unlimited)
        DALSTON_MODEL_CACHE_TTL_HOURS: Max hours since last access (default: 0 = unlimited)
        DALSTON_MODEL_CACHE_SCAN_INTERVAL: Seconds between scans (default: 600)
    """

    def __init__(
        self,
        cache_dirs: list[Path],
        max_gb: float = 0,
        max_age_hours: float = 0,
        scan_interval: int = 600,
        is_model_loaded: Callable[[str], bool] | None = None,
    ) -> None: ...

    @classmethod
    def from_env(
        cls,
        is_model_loaded: Callable[[str], bool] | None = None,
    ) -> DiskCacheEvictor: ...

    def start(self) -> None:
        """Start the background eviction thread."""

    def stop(self) -> None:
        """Stop the background eviction thread."""

    def scan_and_evict(self) -> EvictionResult:
        """Run one eviction pass. Called by background thread and available for manual/test use."""
```

Eviction logic:

1. Walk `cache_dirs` (typically `[MODEL_BASE / "s3-cache", HF_CACHE]`)
2. For each model directory, read `.last_accessed` timestamp (fall back to directory mtime if marker missing)
3. Skip any model whose ID is in the loaded set (`is_model_loaded` callback)
4. **TTL pass**: remove dirs where `now - last_accessed > max_age_hours`
5. **Budget pass**: if total remaining size > `max_gb`, sort by last_accessed ascending, remove oldest until under budget

Additionally, `ensure_local()` triggers `scan_and_evict()` after a fresh download (not on cache hits) so that budget enforcement happens immediately rather than waiting for the next periodic scan.

For HF cache, use `huggingface_hub.scan_cache_dir()` and `delete_revisions()` instead of raw `rmtree`. This respects HF's content-addressed blob deduplication — blobs shared between model revisions are only deleted when no snapshot references them.

For S3 cache, `shutil.rmtree()` is safe since each model dir is self-contained.

---

### 83.3: Wire into Engine Startup

Start the evictor alongside the engine and ModelManager.

**Files modified:**

- `dalston/engine_sdk/base.py` — start `DiskCacheEvictor` in engine init if cache limits are configured
- `dalston/engine_sdk/managers/faster_whisper.py` — pass `is_model_loaded` callback
- `dalston/engine_sdk/managers/hf_transformers.py` — same

**Deliverables:**

The evictor only starts if at least one limit is configured (`DALSTON_MODEL_CACHE_MAX_GB > 0` or `DALSTON_MODEL_CACHE_TTL_HOURS > 0`). When neither is set, behavior is identical to today — no eviction, no overhead.

```python
# In engine init or ModelManager
evictor = DiskCacheEvictor.from_env(
    is_model_loaded=lambda model_id: manager.is_loaded(model_id),
)
evictor.start()
```

---

## Non-Goals

- **Cross-engine coordination** — each engine manages its own cache independently. If two engines share a volume, they may both keep a model alive if either is using it, but eviction is not coordinated between them. This is fine because shared models are rare (only wav2vec2 for align+diarize) and the cost of a redundant download is low.
- **Preemptive download blocking** — this milestone doesn't prevent downloading models that would exceed the budget. An eviction pass runs after each fresh download to reclaim space, but there's no pre-download check ("will this model fit?") since that would require knowing model size before download.
- **Remote cache invalidation** — the gateway/orchestrator don't tell engines to drop cached models. Engines manage their own disk autonomously.

---

## Deployment

No migration required. The feature is opt-in via environment variables. Default values (`0` = unlimited) preserve current behavior exactly.

To enable, add to `docker-compose.yml` or engine environment:

```yaml
DALSTON_MODEL_CACHE_MAX_GB: "50"
DALSTON_MODEL_CACHE_TTL_HOURS: "168"  # 1 week
```

---

## Verification

```bash
# Start engine with aggressive cache limits for testing
docker run --rm --name fw-cache-test \
  -e DALSTON_MODEL_SOURCE=hf \
  -e HF_TOKEN=$HF_TOKEN \
  -e DALSTON_MODEL_CACHE_MAX_GB=0.5 \
  -e DALSTON_MODEL_CACHE_TTL_HOURS=0.01 \
  -e DALSTON_MODEL_CACHE_SCAN_INTERVAL=30 \
  -e DALSTON_ENGINE_ID=faster-whisper \
  -e DALSTON_DEVICE=cpu \
  -e REDIS_URL=redis://redis:6379 \
  -p 9201:9100 \
  dalston/stt-transcribe-faster-whisper:1.0.0

# Transcribe to trigger model download
curl -X POST http://localhost:9201/v1/transcribe \
  -F "file=@tests/audio/test1_speaker1.wav" \
  -F "model=Systran/faster-whisper-base"

# Wait for TTL expiry + scan interval (~1 min with above settings)
sleep 90

# Check logs for eviction
docker logs fw-cache-test 2>&1 | grep -i "evict"
# Should show: disk_cache_evicted model_id=Systran/faster-whisper-base reason=ttl

# Next transcription should re-download
curl -X POST http://localhost:9201/v1/transcribe \
  -F "file=@tests/audio/test1_speaker1.wav" \
  -F "model=Systran/faster-whisper-base"

# Logs should show: model_cache_miss + ensuring_model_from_hf
docker logs fw-cache-test 2>&1 | grep -i "cache_miss\|ensuring_model"
```

---

## Checkpoint

- [x] `.last_accessed` marker written on every `ensure_local()` call
- [x] `DiskCacheEvictor` scans cache dirs on configurable interval
- [x] TTL eviction removes models not accessed within `DALSTON_MODEL_CACHE_TTL_HOURS`
- [x] Budget eviction removes LRU models when total exceeds `DALSTON_MODEL_CACHE_MAX_GB`
- [x] Models currently loaded in `ModelManager` are never evicted from disk
- [x] HF cache eviction uses `scan_cache_dir()` / `delete_revisions()` (not raw rmtree)
- [x] S3 cache eviction uses `shutil.rmtree()`
- [x] No eviction overhead when both limits are 0 (default)
- [x] Unit tests for scan_and_evict with mock filesystem
