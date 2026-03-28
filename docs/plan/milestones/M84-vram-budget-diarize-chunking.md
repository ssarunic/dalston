# M84: VRAM Budget Management, Auto-Calibration & Live Tuning

|                    |                                                              |
| ------------------ | ------------------------------------------------------------ |
| **Goal**           | Engines auto-tune parameters to maximise throughput within a VRAM budget — from calibration through centralised profiles, auto-calibration on first boot, UI visibility, and runtime parameter editing |
| **Duration**       | 14–20 days                                                   |
| **Dependencies**   | M37 (Capacity Management), M76 (Engine Telemetry Depth)      |
| **Deliverable**    | Calibration script, VRAM profiles, runtime budget calculator, diarization chunking, engine startup integration, centralised profile store, auto-calibration on first boot, VRAM visibility in web UI, runtime parameter editing, proactive suggestions |
| **Status**         | In Progress (84.1–84.4 complete)                             |

## User Story

> *"As an operator deploying GPU engines across a fleet of instances, I want engines to automatically calibrate their VRAM parameters on first boot, share those profiles across identical hardware, and let me see and tune parameters from the web console — so that new instances start optimally without manual profiling, and I can adjust parameters without SSH or restarts."*

---

## Outcomes

| Scenario | Current | After M84 |
| -------- | ------- | ---------- |
| 2-hour audio diarization on T4 (16 GB) | pyannote 4.0 OOMs at ~28 min (reconstruction spike) | Audio chunked into 15-min segments, diarized independently, speaker labels merged — completes successfully |
| ONNX + pyannote colocated on g6.xlarge (24 GB) | No VRAM coordination — either engine can grab all VRAM, OOM depends on job ordering | Each engine has a VRAM budget (e.g. 10 GB / 12 GB), parameters auto-tuned to fit, concurrent operation safe |
| Operator deploys on new GPU (A10, L4, T4) | Must manually tune `DALSTON_VAD_BATCH_SIZE`, `DALSTON_VAD_MAX_SPEECH_S` per GPU | Set `DALSTON_VRAM_BUDGET_MB=10000`, engine reads calibration profile and computes optimal params |
| Batch concurrency on transcribe engine | `DALSTON_BATCH_MAX_INFLIGHT=4` is a guess, may OOM with large files | Inflight limit derived from VRAM budget: `(budget - weights) / per_request_activation` |
| Single file in queue, GPU underutilised | `vad_batch_size=1` always, GPU mostly idle between small inference calls | Engine detects shallow queue, switches to solo mode (high batch_size) for full GPU utilisation; switches back to concurrent mode when queue fills |
| First instance on new hardware type | Operator must SSH in, run calibrate_vram.py, copy profile to image or mount | Engine auto-calibrates on first boot (~3 min), saves profile to central store; subsequent instances of same hardware skip calibration |
| Checking engine VRAM usage | SSH into host, run `nvidia-smi`, guess which process is which | Web console shows per-engine VRAM params, current usage, profile source, and solo/concurrent mode |
| Tuning batch size without restart | Edit docker-compose env vars, restart container, wait for model reload | Click "Edit Parameters" in web console, changes apply in seconds without model reload |
| GPU underutilised after deployment | No visibility — operator doesn't know params are conservative | System detects 40% unused VRAM, suggests increasing vad_batch_size; operator clicks "Apply" |
| Profile becomes stale after engine update | No detection — old params may cause OOM or waste GPU | System detects observed vs predicted VRAM divergence, triggers background re-calibration |

---

## Motivation

When two GPU engines share a single GPU (the standard deployment on g4dn/g6 spot instances), neither knows about the other's memory usage. The ONNX Runtime arena allocator and PyTorch's caching allocator are independent — once one grabs VRAM, the other cannot reclaim it. This leads to:

1. **Non-deterministic OOM**: Whichever engine loads first claims more VRAM, leaving insufficient headroom for the other.
2. **pyannote 4.0 reconstruction spike**: Builds a full-resolution diarization matrix proportional to total audio duration. On T4, this OOMs at ~28 min (per [pyannote/pyannote-audio#1897](https://github.com/pyannote/pyannote-audio/issues/1897)).
3. **Manual tuning per GPU**: Operators must guess batch_size and chunk limits for each hardware tier.

Industry context: Google Cloud STT handles 8-hour files with diarization, ElevenLabs handles 10 hours, AssemblyAI handles 10 hours. They all must chunk internally. We currently cap at ~28 min on a T4 with pyannote 4.0.

---

## Architecture

### VRAM Budget Flow

```
┌─────────────────────────────────────────────────────────────────┐
│  Operator config (env vars or deploy template)                  │
│                                                                 │
│  DALSTON_VRAM_BUDGET_MB=10000   (per-engine budget)             │
│  — or —                                                         │
│  DALSTON_VRAM_SHARE=0.45        (fraction of detected total)    │
└──────────────────────┬──────────────────────────────────────────┘
                       │
                       ▼
┌──────────────────────────────────────────────────────────────────┐
│  Engine Startup (VRAMBudget) — runs once                         │
│                                                                  │
│  1. Load calibration profile for (engine_id, model_id, gpu_arch) │
│  2. Compute TWO parameter sets:                                  │
│                                                                  │
│     solo_params:       vad_batch=MAX, inflight=1                 │
│     concurrent_params: vad_batch=1,   inflight=N_MAX             │
│                                                                  │
│  3. Store both on engine instance                                │
│  4. Set static params (diarize chunk limit, max_sessions)        │
│                                                                  │
│  Diarize engine (always static):                                 │
│    DALSTON_MAX_DIARIZE_CHUNK_S = computed                         │
│    DALSTON_DIARIZE_OVERLAP_S = 30 (fixed)                        │
│    DALSTON_BATCH_MAX_INFLIGHT = 1 (always, for diarization)      │
└──────────────────────────────────────────────────────────────────┘
                       │
  Per task:            ▼
┌──────────────────────────────────────────────────────────────────┐
│  Transcribe engine — adaptive per-task selection                  │
│                                                                  │
│  queue_depth = Redis XLEN (O(1))                                 │
│  inflight    = active task counter                               │
│                                                                  │
│  if queue_depth <= 1 and inflight == 0:                           │
│      params = solo_params       → high batch, fast single file   │
│  else:                                                            │
│      params = concurrent_params → low batch, many files          │
│                                                                  │
│  model.with_vad(batch_size=params.vad_batch_size)                │
└──────────────────────────────────────────────────────────────────┘
```

### Diarization Chunking Flow

```
┌─────────────────────────────────────────────────────────────────┐
│  Pyannote Engine — process()                                     │
│                                                                  │
│  audio (2 hours)                                                 │
│    │                                                             │
│    ▼                                                             │
│  1. Check duration vs DALSTON_MAX_DIARIZE_CHUNK_S                │
│     If under limit → diarize directly (existing path)            │
│     If over limit ↓                                              │
│                                                                  │
│  2. Split into overlapping chunks                                │
│     ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐     │
│     │ 0-15min  │  │14.5-30min│  │29.5-45min│  │ ...      │     │
│     └──────────┘  └──────────┘  └──────────┘  └──────────┘     │
│     (30s overlap between chunks)                                 │
│                                                                  │
│  3. Diarize each chunk independently                             │
│     → local speaker labels per chunk (SPEAKER_00, SPEAKER_01)    │
│     → extract speaker embeddings per chunk                       │
│                                                                  │
│  4. Cross-chunk speaker linking                                  │
│     - Extract centroid embedding per (chunk, speaker)            │
│     - Agglomerative clustering on all centroids                  │
│     - Map local labels → global labels                           │
│                                                                  │
│  5. Merge RTTM with global speaker labels                        │
│     - Resolve overlaps at chunk boundaries using overlap region  │
│     - Prefer the chunk where the speaker has more context        │
│                                                                  │
│  6. Return unified DiarizeOutput (same schema as today)          │
└─────────────────────────────────────────────────────────────────┘
```

### Concurrency Model

Understanding what "concurrent" means at each layer:

```
┌─────────────────────────────────────────────────────────────────┐
│  GPU Memory Composition (per engine)                             │
│                                                                  │
│  ┌─────────────────────────────────────────┐                     │
│  │ CUDA context overhead     (~500-800 MB) │ ← fixed, per proc  │
│  ├─────────────────────────────────────────┤                     │
│  │ Model weights             (W MB)        │ ← fixed, loaded 1x │
│  ├─────────────────────────────────────────┤                     │
│  │ Per-request activations   (A MB × N)    │ ← scales with      │
│  │  └ N = concurrent requests              │    concurrency      │
│  ├─────────────────────────────────────────┤                     │
│  │ Framework overhead        (~200 MB)     │ ← arena/cache pool  │
│  └─────────────────────────────────────────┘                     │
│                                                                  │
│  Total = CUDA_CTX + W + (A × N) + FRAMEWORK                     │
│                                                                  │
│  Solving for N:                                                  │
│    N_max = floor((BUDGET - CUDA_CTX - W - FRAMEWORK) / A)        │
│                                                                  │
│  For batch transcription:                                        │
│    A = f(vad_batch_size, max_speech_s)                           │
│    N = DALSTON_BATCH_MAX_INFLIGHT                                │
│                                                                  │
│  For realtime transcription:                                     │
│    A = f(chunk_ms)  — typically small (50-100 MB)                │
│    N = DALSTON_MAX_SESSIONS                                      │
│                                                                  │
│  For diarization:                                                │
│    A = f(audio_duration_s)  — large, scales with duration        │
│    N = 1 always (sequential processing)                          │
└─────────────────────────────────────────────────────────────────┘
```

**Key insight**: For diarization, N=1 is always correct because:

- Activation memory scales with audio duration (not fixed)
- The reconstruction spike is unpredictable without calibration
- Throughput is better served by faster per-file processing than parallelism

For transcription, the tradeoff is:

- **High batch_size, low N**: Fast per-file, low concurrency. Good when queue is shallow.
- **Low batch_size, high N**: Slower per-file, high concurrency. Good when queue is deep.

This is NOT a static choice — it should be **adaptive per task**. When only one file is
queued, `vad_batch_size=1` wastes GPU cycles between small inference calls. When many
files are queued, `vad_batch_size=8` blocks other files from starting.

### Adaptive Batch Strategy

The budget calculator computes two parameter sets at startup. The engine picks
which set to use at task time based on current queue depth:

```
┌──────────────────────────────────────────────────────────┐
│  Startup: VRAMBudget computes two param sets              │
│                                                          │
│  solo_params:   vad_batch_size=MAX, inflight=1           │
│                 (fill GPU from one file)                  │
│                                                          │
│  concurrent_params: vad_batch_size=1, inflight=N_MAX     │
│                     (spread GPU across many files)        │
│                                                          │
│  Both sets guaranteed to fit within VRAM budget.          │
└──────────────────────┬───────────────────────────────────┘
                       │
  Per task:            ▼
┌──────────────────────────────────────────────────────────┐
│  queue_depth = redis XLEN on task queue                   │
│  inflight    = currently processing count                 │
│                                                          │
│  if queue_depth <= 1 and inflight == 0:                   │
│      use solo_params      → fast single-file processing  │
│  else:                                                    │
│      use concurrent_params → maximize files/hour          │
└──────────────────────────────────────────────────────────┘
```

This is safe because `vad_batch_size` is passed per-call to
`model.with_vad(..., batch_size=N)` — it is not baked into the ONNX session.
The engine can change it between tasks with zero overhead.

---

## Steps

### 84.1: VRAM Calibration Script

**Files modified:**

- `dalston/tools/calibrate_vram.py` *(new)*
- `dalston/tools/vram_profiles/` *(new directory)*

**Deliverables:**

A standalone script that measures peak VRAM for an engine across varying input parameters and outputs a calibration profile.

```python
# Usage:
# 1. Start the engine container with GPU
# 2. Run calibration against its HTTP endpoint
python -m dalston.tools.calibrate_vram \
    --engine-url http://localhost:9100 \
    --stage transcribe \
    --model-id parakeet-onnx-tdt-0.6b-v3 \
    --gpu-id 0 \
    --output dalston/tools/vram_profiles/transcribe-parakeet-onnx-tdt-0.6b-v3-T4.json
```

The calibrator:

1. **Generates synthetic audio** at varying durations (numpy noise at 16 kHz, saved as WAV temp files). Durations: 15s, 30s, 60s, 120s, 300s, 600s, 900s.

2. **Measures VRAM** using `pynvml` (device-level, works for both ONNX and PyTorch):
   - Baseline: VRAM after model load, before any inference
   - Peak: Max VRAM during inference (polled at 50ms intervals in a background thread)
   - Post-inference: VRAM after inference completes (checks for memory not released)

3. **Varies parameters** per stage type:

   For `transcribe`:
   - Vary `vad_batch_size`: 1, 2, 4, 8, 16
   - Vary audio duration: 15s, 30s, 60s, 120s (should be ~constant due to VAD chunking)
   - Hold model constant

   For `diarize`:
   - Vary audio duration: 60s, 180s, 300s, 600s, 900s
   - Hold other params constant (pyannote doesn't expose batch_size)

   For `align`:
   - Vary audio duration: 30s, 60s, 120s, 300s

4. **Fits a linear model** via least squares:

   ```
   peak_vram_mb = S + α₁ × param₁ + α₂ × param₂ + ...
   ```

5. **Outputs a JSON profile:**

```json
{
  "schema_version": "1.0",
  "engine_id": "onnx",
  "model_id": "parakeet-onnx-tdt-0.6b-v3",
  "stage": "transcribe",
  "gpu": "Tesla T4",
  "gpu_vram_mb": 15360,
  "cuda_overhead_mb": 650,
  "calibrated_at": "2026-03-23T14:00:00Z",
  "measurements": [
    {"params": {"vad_batch_size": 1, "audio_s": 60}, "peak_vram_mb": 1450},
    {"params": {"vad_batch_size": 8, "audio_s": 60}, "peak_vram_mb": 1820}
  ],
  "model": {
    "weights_mb": 1200,
    "formula": "S + alpha_batch * vad_batch_size",
    "coefficients": {
      "S": 1380,
      "alpha_batch": 55
    },
    "r_squared": 0.97,
    "safety_margin": 0.15
  }
}
```

**Implementation notes:**

- Use `pynvml` (not torch.cuda) so it works for ONNX engines too
- Poll VRAM in a background thread during inference (50ms interval)
- Run each measurement 3 times, take max peak (deterministic worst case)
- VRAM measurement is device-level, so only one engine should be running during calibration

---

### 84.2: VRAM Budget Calculator

**Files modified:**

- `dalston/engine_sdk/vram_budget.py` *(new)*

**Deliverables:**

A runtime module that loads a calibration profile and computes optimal parameters for a given VRAM budget.

```python
class VRAMBudget:
    """Compute engine parameters from a VRAM budget and calibration profile."""

    @classmethod
    def load(
        cls,
        engine_id: str,
        model_id: str,
        gpu_name: str | None = None,
    ) -> VRAMBudget:
        """Load calibration profile.

        Searches for a profile matching (engine_id, model_id, gpu_name).
        Falls back to same-model-different-GPU with a warning.
        Falls back to conservative defaults if no profile exists.
        """
        ...

    def compute_adaptive_params(self, budget_mb: int) -> AdaptiveVRAMParams:
        """Compute both solo and concurrent parameter sets.

        Returns two EngineVRAMParams — one optimised for single-file
        throughput (high batch_size), one for multi-file concurrency
        (low batch_size, high inflight). Both are guaranteed to fit
        within budget_mb including safety margin.
        """
        ...


@dataclass
class EngineVRAMParams:
    """A single parameter set for engine operation."""

    # Transcription parameters
    vad_batch_size: int = 1
    vad_max_speech_s: float = 60.0
    batch_max_inflight: int = 1
    max_sessions: int = 2

    # Diarization parameters
    max_diarize_chunk_s: float = 900.0
    diarize_overlap_s: float = 30.0

    # Metadata
    peak_estimate_mb: int = 0
    headroom_mb: int = 0


@dataclass
class AdaptiveVRAMParams:
    """Two parameter sets for adaptive per-task selection."""

    solo: EngineVRAMParams        # queue_depth <= 1: high batch, N=1
    concurrent: EngineVRAMParams  # queue_depth > 1:  low batch, high N

    budget_mb: int = 0
    profile_source: str = "defaults"  # "calibrated" | "fallback" | "defaults"

    def select(self, queue_depth: int, inflight: int) -> EngineVRAMParams:
        """Pick the right param set based on current load.

        Uses solo params when the GPU would otherwise be idle between
        small inference calls. Switches to concurrent params when
        there is enough work to keep the GPU busy across files.
        """
        if queue_depth <= 1 and inflight == 0:
            return self.solo
        return self.concurrent
```

**How the two sets are computed:**

```
Given VRAM budget B, model weights W, CUDA overhead C, framework overhead F:
  headroom = B × safety_margin
  available = B - C - W - F - headroom

Solo params (fill GPU from one file):
  vad_batch_size = floor(available / alpha_batch)   # from calibration profile
  batch_max_inflight = 1

Concurrent params (spread GPU across files):
  vad_batch_size = 1
  activation_per_file = alpha_batch × 1             # from calibration profile
  batch_max_inflight = floor(available / activation_per_file)
```

The engine always has both sets available and picks per-task with zero overhead
(no model reload, no session recreation — just a different integer passed to
`model.with_vad(batch_size=N)`).

---

### 84.3: Diarization Chunking

**Files modified:**

- `dalston/engine_sdk/diarize_chunking.py` *(new)*
- `engines/stt-diarize/pyannote-4.0/engine.py` — integrate chunking into `process()`

**Deliverables:**

A reusable chunking + speaker-linking module that any diarization engine can use.

```python
@dataclass
class DiarizeChunkConfig:
    max_chunk_s: float = 900.0     # 15 min default
    overlap_s: float = 30.0         # 30s overlap between chunks
    min_chunk_s: float = 60.0       # Don't create tiny trailing chunks
    embedding_model: str = "pyannote/embedding"
    linking_threshold: float = 0.7  # Cosine similarity threshold


class DiarizeChunker:
    """Split long audio for bounded-memory diarization."""

    def __init__(self, config: DiarizeChunkConfig):
        ...

    def needs_chunking(self, audio_duration_s: float) -> bool:
        """Check if audio exceeds chunk limit."""
        return audio_duration_s > self.config.max_chunk_s

    def split_audio(
        self,
        audio_path: Path,
        audio_duration_s: float,
    ) -> list[AudioChunk]:
        """Split audio into overlapping chunks.

        Returns list of AudioChunk(path, offset_s, duration_s).
        Uses ffmpeg for zero-copy splitting.
        """
        ...

    def link_speakers(
        self,
        chunk_results: list[ChunkDiarizeResult],
    ) -> DiarizeOutput:
        """Link speaker labels across chunks using embedding similarity.

        1. Extract centroid embedding per (chunk, speaker)
        2. Agglomerative clustering on all centroids
        3. Map local labels → global labels
        4. Merge RTTM, resolving chunk boundary overlaps
        """
        ...
```

**Audio splitting strategy:**

- Use ffmpeg (`-ss` / `-t` flags) for zero-copy splitting — no re-encoding
- Overlap region: 30s (configurable). Enough for speaker embedding extraction at boundaries.
- Trailing chunk: if remaining audio < `min_chunk_s`, extend the previous chunk instead of creating a tiny one.

**Speaker linking algorithm:**

1. For each chunk, extract one centroid embedding per detected speaker using pyannote's embedding model
2. Build a distance matrix (cosine distance) across all (chunk, speaker) centroids
3. Agglomerative clustering with `distance_threshold` (not fixed `n_clusters`) — automatically determines how many global speakers exist
4. Map local `SPEAKER_00` per chunk to global `SPEAKER_A`, `SPEAKER_B`, etc.

**Boundary resolution:**

- In the 30s overlap region, both chunks produce speaker labels
- Use the chunk where each speaker has more total speaking time (better embedding quality)
- If speakers disagree at the boundary, prefer the chunk with the earlier start (left chunk) up to the midpoint of the overlap, then switch to the right chunk

**Integration into pyannote engine:**

```python
# engines/stt-diarize/pyannote-4.0/engine.py

def process(self, task_request, ctx):
    audio_path = ...
    audio_duration_s = get_audio_duration(audio_path)

    chunker = DiarizeChunker(DiarizeChunkConfig(
        max_chunk_s=float(os.environ.get("DALSTON_MAX_DIARIZE_CHUNK_S", 900)),
    ))

    if not chunker.needs_chunking(audio_duration_s):
        # Existing path — diarize directly
        return self._diarize_full(audio_path, ...)

    # Chunked path
    chunks = chunker.split_audio(audio_path, audio_duration_s)
    chunk_results = []
    for chunk in chunks:
        result = self._diarize_full(chunk.path, ...)
        chunk_results.append(ChunkDiarizeResult(
            chunk=chunk,
            diarization=result,
            embeddings=self._extract_embeddings(chunk.path, result),
        ))
        # Free GPU memory between chunks
        torch.cuda.empty_cache()

    return chunker.link_speakers(chunk_results)
```

---

### 84.4: Engine Startup & Per-Task Adaptive Integration

**Files modified:**

- `dalston/engine_sdk/runner.py` — compute adaptive params at startup, store on engine instance
- `dalston/engine_sdk/inference/onnx_inference.py` — accept `vad_batch_size` per call instead of reading env once
- `engines/stt-diarize/pyannote-4.0/engine.py` — read computed chunk limit

**Deliverables:**

Two-phase integration: startup computes the param sets, per-task picks which set to use.

**Phase A — Startup (once):**

1. Detect available VRAM via `pynvml`
2. Read VRAM budget from env: `DALSTON_VRAM_BUDGET_MB` or `DALSTON_VRAM_SHARE`
3. Load calibration profile for (engine_id, model_id, gpu)
4. Compute both param sets via `VRAMBudget.compute_adaptive_params()`
5. Store `AdaptiveVRAMParams` on the engine instance
6. Set static params (diarize chunk limit, max_sessions) as env vars
7. Log both computed param sets

```python
# In runner.py, during engine initialization:

def _init_vram_budget(engine_id: str, model_id: str) -> AdaptiveVRAMParams | None:
    """Compute adaptive VRAM params at startup."""
    budget_mb = _resolve_vram_budget()
    if budget_mb is None:
        logger.info("vram_budget_skip", reason="no budget configured")
        return None

    vram = VRAMBudget.load(engine_id, model_id)
    adaptive = vram.compute_adaptive_params(budget_mb)

    # Static params that don't change per task
    _set_if_absent("DALSTON_MAX_DIARIZE_CHUNK_S", str(adaptive.solo.max_diarize_chunk_s))
    _set_if_absent("DALSTON_MAX_SESSIONS", str(adaptive.concurrent.max_sessions))

    logger.info(
        "vram_budget_computed",
        budget_mb=budget_mb,
        solo={"vad_batch": adaptive.solo.vad_batch_size,
              "inflight": adaptive.solo.batch_max_inflight,
              "peak_mb": adaptive.solo.peak_estimate_mb},
        concurrent={"vad_batch": adaptive.concurrent.vad_batch_size,
                    "inflight": adaptive.concurrent.batch_max_inflight,
                    "peak_mb": adaptive.concurrent.peak_estimate_mb},
        profile_source=adaptive.profile_source,
    )
    return adaptive


def _resolve_vram_budget() -> int | None:
    """Resolve VRAM budget from env vars."""
    if explicit := os.environ.get("DALSTON_VRAM_BUDGET_MB"):
        return int(explicit)

    share = os.environ.get("DALSTON_VRAM_SHARE")
    if share:
        import pynvml
        pynvml.nvmlInit()
        handle = pynvml.nvmlDeviceGetHandleByIndex(0)
        total = pynvml.nvmlDeviceGetMemoryInfo(handle).total // (1024 * 1024)
        pynvml.nvmlShutdown()
        return int(total * float(share))

    return None  # No budget configured — use existing defaults
```

**Phase B — Per task (every task):**

Before each inference call, the engine checks queue depth and selects params:

```python
# In the batch engine's process loop (e.g. batch_engine.py):

def process(self, task_request, ctx):
    if self._adaptive_params is not None:
        queue_depth = self._get_queue_depth()  # Redis XLEN
        inflight = self._get_inflight_count()  # active tasks counter
        params = self._adaptive_params.select(queue_depth, inflight)
        vad_batch_size = params.vad_batch_size
    else:
        # Fallback: read from env (existing behaviour)
        vad_batch_size = int(os.environ.get("DALSTON_VAD_BATCH_SIZE", 8))

    result = self._inference.transcribe(
        audio_path,
        model_id,
        vad_batch_size=vad_batch_size,  # NEW: per-call override
    )
```

This requires a small change to `OnnxInference.transcribe()` and
`_transcribe_with_vad()` — accept an optional `vad_batch_size` parameter
instead of always reading from `os.environ`:

```python
# onnx_inference.py — change signature:

def _transcribe_with_vad(
    self,
    model: Any,
    audio_path: str,
    vad_batch_size: int | None = None,  # NEW: per-call override
) -> OnnxTranscriptionResult:
    vad_batch_size = vad_batch_size or int(
        os.environ.get("DALSTON_VAD_BATCH_SIZE", _DEFAULT_VAD_BATCH_SIZE)
    )
    # ... rest unchanged
```

**Queue depth query:**

```python
def _get_queue_depth(self) -> int:
    """Check pending tasks in this engine's Redis queue.

    Cheap operation — XLEN is O(1) in Redis.
    """
    stream_key = f"dalston:tasks:{self._stage}"
    return self._redis.xlen(stream_key)
```

**Operator interface:**

```yaml
# docker-compose.gpu.yml
services:
  stt-transcribe-onnx:
    environment:
      DALSTON_VRAM_SHARE: "0.45"          # 45% of GPU VRAM
      # — or —
      DALSTON_VRAM_BUDGET_MB: "10000"     # Explicit 10 GB

  stt-diarize-pyannote-4.0:
    environment:
      DALSTON_VRAM_SHARE: "0.55"          # 55% of GPU VRAM
```

For `start-gpu-combo.sh`, add to both containers:

```bash
-e DALSTON_VRAM_SHARE=0.45   # transcriber
-e DALSTON_VRAM_SHARE=0.55   # diarizer
```

**Backward compatibility:** When no `DALSTON_VRAM_SHARE` or `DALSTON_VRAM_BUDGET_MB`
is set, `_adaptive_params` is `None` and the engine falls back to reading env vars
exactly as it does today. Zero behaviour change for existing deployments.

---

### 84.5: Centralised Profile Store

**Files modified:**

- `dalston/db/models.py` — add `VRAMProfileModel`
- `dalston/db/migrations/versions/xxxx_add_vram_profiles.py` *(new)*
- `dalston/gateway/api/console.py` — add profile CRUD endpoints
- `dalston/engine_sdk/vram_budget.py` — query central store before local files

**Deliverables:**

A Postgres table that stores calibration profiles keyed by `(engine_id, model_id, gpu_model, runtime_fingerprint)`, so any engine instance can look up an existing profile instead of requiring a local JSON file.

```python
# dalston/db/models.py

class VRAMProfileModel(Base):
    """Persisted VRAM calibration profile.

    Keyed by (engine_id, model_id, gpu_model, runtime_fingerprint).
    Any engine instance on matching hardware skips calibration and
    reads this profile at startup.
    """
    __tablename__ = "vram_profiles"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    engine_id: Mapped[str] = mapped_column(String(128))
    model_id: Mapped[str] = mapped_column(String(256))
    gpu_model: Mapped[str] = mapped_column(String(128))       # "Tesla T4"
    gpu_vram_mb: Mapped[int]
    runtime_fingerprint: Mapped[str] = mapped_column(String(64))  # hash of runtime versions
    schema_version: Mapped[str] = mapped_column(String(16), default="1.0")
    coefficients: Mapped[dict] = mapped_column(JSONB)          # {S: 3995, alpha_batch: 55}
    formula: Mapped[str] = mapped_column(String(256))
    r_squared: Mapped[float]
    safety_margin: Mapped[float] = mapped_column(default=0.15)
    measurements: Mapped[list] = mapped_column(JSONB)          # raw data points
    manual_overrides: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    calibrated_at: Mapped[datetime]
    calibrated_by: Mapped[str] = mapped_column(String(256))    # "auto" | instance_id

    __table_args__ = (
        UniqueConstraint(
            "engine_id", "model_id", "gpu_model", "runtime_fingerprint",
            name="uq_vram_profile_key",
        ),
    )
```

**Runtime fingerprint:** A short hash computed at engine startup from the versions that affect VRAM behaviour:

```python
# dalston/engine_sdk/vram_budget.py

def compute_runtime_fingerprint(engine_id: str, model_id: str) -> str:
    """Hash of runtime versions that affect VRAM characteristics.

    Changes to any of these components invalidate cached profiles:
    - ONNX Runtime / PyTorch version
    - CUDA runtime version
    - Model file checksum (first 64KB)
    """
    import hashlib
    parts: list[str] = []

    # CUDA version
    try:
        import pynvml
        pynvml.nvmlInit()
        parts.append(f"cuda:{pynvml.nvmlSystemGetCudaDriverVersion()}")
        pynvml.nvmlShutdown()
    except Exception:
        parts.append("cuda:none")

    # Framework version
    try:
        import onnxruntime
        parts.append(f"ort:{onnxruntime.__version__}")
    except ImportError:
        pass
    try:
        import torch
        parts.append(f"torch:{torch.__version__}")
    except ImportError:
        pass

    # Model file hash (first 64KB for speed)
    model_path = os.environ.get("DALSTON_MODEL_PATH", "")
    if model_path and Path(model_path).exists():
        with open(model_path, "rb") as f:
            parts.append(f"model:{hashlib.sha256(f.read(65536)).hexdigest()[:12]}")

    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]
```

**Profile lookup order** (modified `VRAMBudget.load()`):

```
1. Central store: exact match on (engine_id, model_id, gpu_model, runtime_fingerprint)
2. Central store: same (engine_id, model_id, gpu_model), different fingerprint
   → use it but flag as "stale", trigger background re-calibration
3. Local file: dalston/tools/vram_profiles/ (existing path, for development)
4. Fallback: conservative defaults
```

**Console API endpoints:**

```
GET  /api/console/vram-profiles                          — list all profiles
GET  /api/console/vram-profiles/{engine_id}/{model_id}   — profiles for an engine+model
DELETE /api/console/vram-profiles/{id}                    — delete a stale profile
```

---

### 84.6: Auto-Calibration on First Boot

**Files modified:**

- `dalston/engine_sdk/runner.py` — add self-calibration phase to startup
- `dalston/engine_sdk/self_calibrate.py` *(new)* — stripped-down calibration for in-process use
- `dalston/engine_sdk/admission.py` — add calibration lock

**Deliverables:**

When an engine starts on hardware with no matching profile in the central store, it runs a self-calibration phase before accepting work. The calibration acquires all admission capacity (blocking batch tasks and RT sessions) and measures peak VRAM for the engine's parameter space.

```
┌─────────────────────────────────────────────────────────────────┐
│  Engine Startup Flow (expanded)                                  │
│                                                                  │
│  1. Load model (existing)                                        │
│  2. Detect GPU model + compute runtime fingerprint               │
│  3. Query central store for profile                              │
│     ├─ Found (exact fingerprint) → use profile, skip to step 6  │
│     ├─ Found (stale fingerprint) → use profile, schedule         │
│     │   background re-calibration after startup                  │
│     └─ Not found → step 4                                        │
│  4. Acquire calibration lock (capacity → 0)                      │
│     → Registry status: "calibrating"                             │
│     → Orchestrator won't route work here                         │
│  5. Run self-calibration (~2-5 min)                              │
│     → Synthetic audio through actual inference path              │
│     → VRAM monitoring via pynvml                                 │
│     → Fit linear model, compute coefficients                     │
│  6. Save profile to central store                                │
│  7. Compute adaptive params from profile                         │
│  8. Release calibration lock (capacity → normal)                 │
│     → Registry status: "idle"                                    │
│  9. Start accepting work                                         │
└─────────────────────────────────────────────────────────────────┘
```

**Self-calibration module** — a streamlined version of `calibrate_vram.py` designed to run in-process:

```python
# dalston/engine_sdk/self_calibrate.py

@dataclass
class SelfCalibrationConfig:
    """Config for in-process self-calibration."""
    stage: str                          # "transcribe" | "diarize" | "align"
    # Transcribe-specific
    audio_durations_s: list[int] = field(default_factory=lambda: [15, 30, 60])
    vad_batch_sizes: list[int] = field(default_factory=lambda: [1, 4, 8, 16])
    # Diarize-specific
    diarize_durations_s: list[int] = field(default_factory=lambda: [60, 180, 300, 600])
    repeats: int = 2                    # fewer repeats than manual calibration
    poll_interval_ms: int = 50


async def run_self_calibration(
    engine: Engine,
    config: SelfCalibrationConfig,
    gpu_index: int = 0,
) -> dict:
    """Run in-process calibration by calling engine.process() with synthetic audio.

    Returns a profile dict compatible with VRAMBudget.load() format.
    Runs synchronously in the engine process — no HTTP, no Redis.
    """
    ...
```

**Key difference from `calibrate_vram.py`:** Self-calibration calls `engine.process()` directly (in-process), not via HTTP. This avoids needing the engine's HTTP server to be running and eliminates network overhead. The VRAM monitor thread runs in parallel.

**Reduced measurement matrix:** Self-calibration uses fewer data points than manual calibration (2 repeats instead of 3, fewer durations) to keep startup time under 3 minutes. The tradeoff is slightly lower R² — compensated by a higher safety margin for auto-calibrated profiles (20% vs 15% for manual).

**Calibration lock via admission controller:**

```python
# dalston/engine_sdk/admission.py — addition

class AdmissionController:
    def acquire_calibration_lock(self) -> None:
        """Block all new work during calibration.

        Sets effective capacity to 0. Existing in-flight work (if any)
        continues but no new tasks/sessions are admitted.
        """
        self._calibrating = True

    def release_calibration_lock(self) -> None:
        self._calibrating = False

    def can_accept_batch(self) -> bool:
        if self._calibrating:
            return False
        # ... existing logic

    def can_accept_rt(self) -> bool:
        if self._calibrating:
            return False
        # ... existing logic
```

---

### 84.7: VRAM Visibility in Web Console

**Files modified:**

- `dalston/common/registry.py` — extend `EngineRecord` with VRAM fields
- `dalston/engine_sdk/runner.py` — include VRAM params in heartbeat
- `dalston/gateway/api/console.py` — add VRAM detail endpoint, VRAM history
- `web/src/pages/EngineDetail.tsx` — add VRAM Budget panel
- `web/src/pages/Infrastructure.tsx` — add per-engine VRAM badges
- `web/src/api/types.ts` — add VRAM types
- `web/src/hooks/useVRAMHistory.ts` *(new)*

**Deliverables:**

Extend the engine heartbeat and web console to show VRAM budget parameters, current GPU usage, profile source, and usage history.

**Registry additions** — new fields on `EngineRecord`:

```python
# dalston/common/registry.py — additions to EngineRecord

    # M84: VRAM budget fields
    vram_budget_mb: int = 0
    vram_profile_source: str = ""       # "calibrated" | "defaults" | "stale" | "manual_override"
    vram_active_mode: str = ""          # "solo" | "concurrent"
    vram_params_json: str = ""          # JSON of current EngineVRAMParams
    vram_solo_params_json: str = ""     # JSON of solo EngineVRAMParams
    vram_concurrent_params_json: str = ""  # JSON of concurrent EngineVRAMParams
```

These are included in the heartbeat so the gateway always has current state.

**VRAM history** — a Redis ring buffer per engine instance:

```
dalston:engine:vram_history:{instance}    LIST of (timestamp, vram_used_mb) tuples
```

Each heartbeat (10s) appends the current `gpu_memory_used` to the list, trimmed to 180 entries (30 min window). The gateway reads this for the sparkline chart.

**Console API additions:**

```
GET /api/console/engines/{instance}/vram
  → {
      budget_mb: 6912,
      gpu_model: "Tesla T4",
      gpu_vram_mb: 15360,
      profile_source: "calibrated",
      calibrated_at: "2026-03-26T16:54:16Z",
      runtime_fingerprint: "a3f8b2c1...",
      safety_margin: 0.15,
      active_mode: "solo",
      solo_params: { vad_batch_size: 8, batch_max_inflight: 1, peak_estimate_mb: 4435, ... },
      concurrent_params: { vad_batch_size: 1, batch_max_inflight: 3, peak_estimate_mb: 6780, ... },
      coefficients: { S: 3995, alpha_batch: 55 },
      history: [ { ts: "...", vram_mb: 4102 }, ... ]  // last 30 min
    }
```

**Web UI — Engine Detail page VRAM panel:**

```
┌─ VRAM Budget ──────────────────────────────────────────────┐
│ GPU: Tesla T4 (15,360 MB)                                  │
│ Budget: 6,912 MB (45% share)     Profile: calibrated       │
│ CUDA overhead: 448 MB             Calibrated: 2h ago       │
│ Available for inference: 6,464 MB  Safety margin: 15%      │
│                                                             │
│ ┌─ Current Parameters ──────────────────────────────────┐  │
│ │ Mode: solo (queue empty)                              │  │
│ │                                                       │  │
│ │ vad_batch_size:      8    ← solo: 8  / concurrent: 1 │  │
│ │ batch_max_inflight:  1    ← solo: 1  / concurrent: 3 │  │
│ │ max_sessions:        2                                │  │
│ │ rt_reservation:      2                                │  │
│ │ total_capacity:      6                                │  │
│ │                                                       │  │
│ │ Peak VRAM estimate:  4,435 MB (solo)                  │  │
│ │ Headroom:            2,029 MB                         │  │
│ └───────────────────────────────────────────────────────┘  │
│                                                             │
│ ┌─ GPU Usage (last 30 min) ─────────────────────────────┐  │
│ │ ████████████░░░░░░░░ 4,102 / 15,360 MB (26.7%)       │  │
│ │ ▁▂▃▃▅▇█▇▅▃▂▁▂▅▇█▇▅▃  (sparkline)                     │  │
│ └───────────────────────────────────────────────────────┘  │
│                                                             │
│ [Recalibrate]  [Edit Parameters]                           │
└────────────────────────────────────────────────────────────┘
```

**Infrastructure page additions:**

- Per-engine row gains a small VRAM usage bar (inline, same row as status dot)
- Tooltip on hover shows: `profile_source`, `active_mode`, `budget_mb`
- If VRAM shares on a node sum > 95%: red "VRAM over-committed" badge on node header

---

### 84.8: Runtime Parameter Editing

**Files modified:**

- `dalston/gateway/api/console.py` — add override endpoint
- `dalston/common/registry.py` — add override pubsub channel
- `dalston/engine_sdk/runner.py` — subscribe to override channel, apply changes
- `web/src/pages/EngineDetail.tsx` — add "Edit Parameters" modal

**Deliverables:**

Allow operators to override computed VRAM parameters at runtime without restarting the engine container. Changes take effect within one heartbeat interval (10s).

**Override flow:**

```
┌─────────────────────────────────────────────────────────────┐
│  Web Console                                                 │
│  [Edit Parameters] → modal with sliders/inputs               │
│                                                              │
│  POST /api/console/engines/{instance}/vram/override          │
│    { "vad_batch_size_solo": 12, "batch_max_inflight": 4 }   │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       ▼
┌──────────────────────────────────────────────────────────────┐
│  Gateway                                                      │
│  1. Validate: compute estimated peak from coefficients        │
│     → reject if peak > gpu_vram_mb (hard limit)              │
│     → warn if peak > 90% of gpu_vram_mb                      │
│  2. Publish to Redis: dalston:engine:config:{instance}       │
│  3. Store override in vram_profiles.manual_overrides (Postgres)│
└──────────────────────┬──────────────────────────────────────┘
                       │
                       ▼
┌──────────────────────────────────────────────────────────────┐
│  Engine Runner (heartbeat loop)                               │
│  1. Subscribe to dalston:engine:config:{instance}            │
│  2. On message: validate + apply new AdaptiveVRAMParams      │
│  3. Log: vram_params_overridden, old=..., new=...            │
│  4. Next heartbeat: vram_profile_source = "manual_override"  │
│                                                              │
│  On container restart:                                        │
│  → Load profile from central store                           │
│  → Apply manual_overrides column if present                  │
│  → manual_overrides survive restart                          │
└──────────────────────────────────────────────────────────────┘
```

**Override API:**

```python
class VRAMOverrideRequest(BaseModel):
    """Runtime parameter override."""
    vad_batch_size_solo: int | None = None
    vad_batch_size_concurrent: int | None = None
    batch_max_inflight: int | None = None
    max_sessions: int | None = None
    max_diarize_chunk_s: float | None = None
    rt_reservation: int | None = None
    total_capacity: int | None = None
    safety_margin: float | None = None

class VRAMOverrideResponse(BaseModel):
    applied: bool
    estimated_peak_solo_mb: int
    estimated_peak_concurrent_mb: int
    headroom_mb: int
    warnings: list[str]          # e.g. ["headroom below 10%"]
```

**Safety guardrails:**

- Gateway computes estimated peak VRAM using stored coefficients before applying
- Hard reject if estimated peak > 100% of `gpu_vram_mb`
- Warning in response if estimated peak > 90% of `gpu_vram_mb`
- UI shows live estimate as operator adjusts sliders
- "Reset to Auto" button clears overrides, recomputes from profile

**Edit Parameters modal:**

```
┌─ Override VRAM Parameters ─────────────────────────────────┐
│                                                             │
│ ⚠ Overrides replace auto-computed values. Persistent       │
│   across restarts until cleared.                            │
│                                                             │
│ vad_batch_size (solo):      [  8  ] ▼  (auto: 8)          │
│ vad_batch_size (concurrent):[  1  ] ▼  (auto: 1)          │
│ batch_max_inflight:         [  3  ] ▼  (auto: 3)          │
│ max_sessions:               [  2  ] ▼  (auto: 2)          │
│ rt_reservation:             [  2  ] ▼  (auto: 2)          │
│ total_capacity:             [  6  ] ▼  (auto: 6)          │
│ safety_margin:              [ 0.15] ▼  (auto: 0.15)       │
│                                                             │
│ Estimated peak (solo):       4,435 MB                      │
│ Estimated peak (concurrent): 6,780 MB                      │
│ GPU headroom:                 132 MB ⚠ LOW                 │
│                                                             │
│              [Reset to Auto]  [Apply]                       │
└────────────────────────────────────────────────────────────┘
```

---

### 84.9: Proactive Suggestions & Drift Detection

**Files modified:**

- `dalston/engine_sdk/runner.py` — track per-task peak VRAM
- `dalston/gateway/services/vram_advisor.py` *(new)* — analysis loop
- `dalston/gateway/api/console.py` — suggestions endpoint
- `web/src/pages/EngineDetail.tsx` — suggestion banner
- `web/src/pages/Infrastructure.tsx` — suggestion badges

**Deliverables:**

A background analysis loop in the gateway that compares observed VRAM usage against profile predictions, generates tuning suggestions, and detects profile drift.

**Per-task peak tracking** — engines record peak VRAM after each task:

```python
# dalston/engine_sdk/runner.py — after task completion

def _record_task_vram_peak(self, task_id: str, peak_mb: int) -> None:
    """Append peak VRAM to ring buffer for advisor analysis."""
    key = f"dalston:engine:vram_peaks:{self._instance}"
    entry = json.dumps({"ts": datetime.now(UTC).isoformat(), "peak_mb": peak_mb, "task_id": task_id})
    pipe = self._redis.pipeline()
    pipe.rpush(key, entry)
    pipe.ltrim(key, -100, -1)  # keep last 100
    pipe.execute()
```

**VRAM advisor service** — runs every 5 minutes in the gateway:

```python
# dalston/gateway/services/vram_advisor.py

@dataclass
class VRAMSuggestion:
    instance: str
    type: str               # "increase_throughput" | "reduce_oom_risk" | "profile_drift"
    severity: str           # "info" | "warning"
    message: str
    current_params: dict
    suggested_params: dict | None
    dismissed: bool = False


class VRAMAdvisor:
    """Analyses observed VRAM usage and generates tuning suggestions."""

    async def analyse_all(self) -> list[VRAMSuggestion]:
        """Run analysis for all active engine instances."""
        suggestions = []
        for instance in await self._registry.get_all_instances():
            suggestion = await self._analyse_instance(instance)
            if suggestion:
                suggestions.append(suggestion)
        return suggestions

    async def _analyse_instance(self, instance: str) -> VRAMSuggestion | None:
        peaks = await self._get_recent_peaks(instance, count=50)
        if len(peaks) < 10:
            return None  # not enough data

        profile = await self._get_profile(instance)
        params = await self._get_current_params(instance)
        budget_mb = await self._get_budget(instance)

        observed_max = max(p["peak_mb"] for p in peaks)
        observed_avg = mean(p["peak_mb"] for p in peaks)

        # --- Underutilisation detection ---
        headroom_pct = (budget_mb - observed_max) / budget_mb
        if headroom_pct > 0.30:
            new_params = self._recompute_with_target(
                profile, target_headroom=0.15, budget_mb=budget_mb,
            )
            return VRAMSuggestion(
                instance=instance,
                type="increase_throughput",
                severity="info",
                message=(
                    f"GPU has {headroom_pct:.0%} unused VRAM over last "
                    f"{len(peaks)} tasks. Could increase vad_batch_size "
                    f"from {params['vad_batch_size']} to "
                    f"{new_params['vad_batch_size']} for higher throughput."
                ),
                current_params=params,
                suggested_params=new_params,
            )

        # --- OOM risk detection ---
        if headroom_pct < 0.05:
            new_params = self._recompute_with_target(
                profile, target_headroom=0.20, budget_mb=budget_mb,
            )
            return VRAMSuggestion(
                instance=instance,
                type="reduce_oom_risk",
                severity="warning",
                message=(
                    f"GPU headroom is only {headroom_pct:.0%}. "
                    f"Reduce vad_batch_size to avoid OOM on longer inputs."
                ),
                current_params=params,
                suggested_params=new_params,
            )

        # --- Profile drift detection ---
        if profile and profile.get("coefficients"):
            predicted = self._predict_peak(profile, params)
            drift = abs(observed_avg - predicted) / predicted
            if drift > 0.10:
                return VRAMSuggestion(
                    instance=instance,
                    type="profile_drift",
                    severity="warning",
                    message=(
                        f"Observed VRAM ({observed_avg:.0f} MB avg) deviates "
                        f"{drift:.0%} from profile prediction ({predicted:.0f} MB). "
                        f"Profile may be stale — consider re-calibrating."
                    ),
                    current_params=params,
                    suggested_params=None,
                )

        return None
```

**Console API:**

```
GET /api/console/vram-suggestions
  → { suggestions: [ { instance, type, severity, message, suggested_params, ... } ] }

POST /api/console/vram-suggestions/{instance}/apply
  → applies suggested_params as override (same flow as 84.8)

POST /api/console/vram-suggestions/{instance}/dismiss
  → marks suggestion as dismissed for this instance (until next analysis cycle)

POST /api/console/engines/{instance}/recalibrate
  → triggers background re-calibration (publishes command to engine via pubsub)
```

**Web UI — suggestion banner on Engine Detail page:**

```
┌─────────────────────────────────────────────────────────────┐
│ 💡 GPU has 40% unused VRAM over last 50 tasks.              │
│    Increase vad_batch_size from 4 → 8 for higher throughput.│
│                                          [Apply]  [Dismiss] │
└─────────────────────────────────────────────────────────────┘
```

**Infrastructure page:** Small badge on engines with pending suggestions (orange dot for info, red dot for warnings).

**Drift-triggered re-calibration:** When a `profile_drift` suggestion is generated, the advisor can optionally auto-trigger re-calibration if the engine is idle. The engine receives a `recalibrate` command via pubsub, acquires the calibration lock, runs self-calibration (same as 84.6), and saves the updated profile. This happens without operator intervention.

---

## Non-Goals

- **ONNX `gpu_mem_limit` enforcement** — The calibrator tells the engine what params are safe; we don't need hard VRAM caps via ONNX session options. Hard caps cause cryptic failures; computed params prevent overuse gracefully.
- **Dynamic VRAM rebalancing** — Engines get a static budget at startup. Dynamic rebalancing (idle engine yields VRAM) is a separate concern and requires model CPU offloading (future milestone).
- **Multi-GPU partitioning** — This milestone assumes single-GPU instances. MIG/MPS partitioning is out of scope.
- **Calibrating all engine × model × GPU combinations** — Ship profiles for the primary deployment target (Parakeet TDT 0.6B v3 ONNX + pyannote 4.0 on T4 and L4). Other combinations use auto-calibrated on first boot.
- **Cross-engine VRAM coordination at runtime** — No runtime protocol between containers. Static budget split at deploy time is sufficient.
- **Auto-negotiation of VRAM shares** — Operators set `DALSTON_VRAM_SHARE` per container. The system validates (warns on over-commitment) but does not auto-negotiate between engines on the same GPU.
- **Kubernetes GPU scheduling** — This milestone targets Docker Compose and standalone deployments. K8s device plugin integration is a separate concern.

---

## Deployment

### Ordering

**Phase A (complete):**

1. **84.1** (calibration script) — built and run independently
2. **84.2** (budget calculator) — depends on 84.1's output format
3. **84.3** (diarize chunking) — independent of 84.1/84.2, built in parallel
4. **84.4** (startup integration) — depends on 84.2 and 84.3

**Phase B (central store + auto-calibration):**

5. **84.5** (centralised profile store) — requires Postgres migration, deploy gateway first
6. **84.6** (auto-calibration) — depends on 84.5 for profile storage; engine images must be rebuilt

**Phase C (UI + runtime tuning):**

7. **84.7** (VRAM visibility) — depends on 84.5 for profile data; gateway + web deploy
8. **84.8** (runtime editing) — depends on 84.7 for UI; gateway + engine + web deploy
9. **84.9** (proactive suggestions) — depends on 84.7 and 84.8; gateway + engine + web deploy

### Migration

**Phase A (existing):**

- All changes are **additive**. Without `DALSTON_VRAM_BUDGET_MB` or `DALSTON_VRAM_SHARE`, engines behave exactly as today.
- Diarize chunking activates only when audio exceeds `DALSTON_MAX_DIARIZE_CHUNK_S` (default 900s = 15 min).
- Ship initial calibration profiles for T4 and L4 alongside the code.

**Phase B:**

- **Postgres migration**: Add `vram_profiles` table. Non-destructive — no existing tables modified.
- **Profile migration**: On first startup after deploy, `VRAMBudget.load()` checks central store first, falls back to local files. Existing local profiles continue to work. Engines that find a local profile but no central one automatically save it to the central store (one-time migration).
- **Auto-calibration**: Only triggers when no profile exists for the hardware. Existing deployments with local profiles are unaffected.

**Phase C:**

- **Registry fields**: New VRAM fields on `EngineRecord` default to empty strings — old engines that don't send them are unaffected.
- **VRAM history**: New Redis keys, no migration needed.
- **Override pubsub**: Engines that haven't been updated simply don't subscribe — overrides are a no-op until they do.

### Rollback

Each phase is independently rollable:

- Phase B: Drop `vram_profiles` table, engines fall back to local files + defaults
- Phase C: Remove UI components, engines ignore pubsub channels they're not subscribed to

---

## Verification

### Phase A (84.1–84.4)

```bash
# 1. Run calibration on a T4 instance
make dev-gpu

python -m dalston.tools.calibrate_vram \
    --engine-url http://localhost:9100 \
    --stage transcribe \
    --model-id parakeet-onnx-tdt-0.6b-v3 \
    --gpu-id 0 \
    --output /tmp/profile.json

cat /tmp/profile.json | jq '.model.coefficients'
# Expect: {"S": ~1200-1500, "alpha_batch": ~40-80}

# 2. Verify budget calculator
python -c "
from dalston.engine_sdk.vram_budget import VRAMBudget
b = VRAMBudget.from_profile('/tmp/profile.json')
p = b.compute_params(budget_mb=10000)
print(f'batch_size={p.vad_batch_size}, inflight={p.batch_max_inflight}, headroom={p.headroom_mb}MB')
assert p.peak_estimate_mb <= 10000
assert p.headroom_mb > 0
"

# 3. Verify diarize chunking with a long file
ffmpeg -f lavfi -i "sine=frequency=440:duration=1800" -ar 16000 -ac 1 /tmp/long_test.wav

curl -s -X POST http://localhost:9102/v1/diarize \
    -F "file=@/tmp/long_test.wav" | jq '.speakers | length'
# Expect: completes without OOM, returns speaker list

# 4. Verify startup auto-tuning
docker compose logs stt-transcribe-onnx | grep "vram_budget_applied"
# Expect: log line showing computed params

# 5. Verify 2-hour file does not OOM
ffmpeg -f lavfi -i "sine=frequency=440:duration=7200" -ar 16000 -ac 1 /tmp/2hr_test.wav

curl -s -X POST http://localhost:9102/v1/diarize \
    -F "file=@/tmp/2hr_test.wav" | jq '.duration_s'
# Expect: ~7200, completes successfully
```

### Phase B (84.5–84.6)

```bash
# 6. Verify central profile store
make dev-gpu

# Check migration ran
docker compose exec -T postgres psql -U dalston -c "\d vram_profiles"
# Expect: table with columns id, engine_id, model_id, gpu_model, ...

# Check profile was migrated from local file
curl -s http://localhost:8000/api/console/vram-profiles \
    -H "Authorization: Bearer $DALSTON_API_KEY" | jq '.profiles | length'
# Expect: >= 1

# 7. Verify auto-calibration on unknown hardware
# Start engine with no matching profile (simulate by clearing profile store)
docker compose exec -T postgres psql -U dalston \
    -c "DELETE FROM vram_profiles WHERE gpu_model = 'Tesla T4'"

# Restart engine
docker compose restart stt-transcribe-onnx

# Watch logs for calibration
docker compose logs -f stt-transcribe-onnx 2>&1 | grep -E "calibrat|vram"
# Expect: "no_vram_profile_found" → "self_calibration_started" → "self_calibration_complete" → "vram_budget_computed"

# Verify profile was saved to central store
curl -s http://localhost:8000/api/console/vram-profiles \
    -H "Authorization: Bearer $DALSTON_API_KEY" | jq '.profiles[] | select(.gpu_model == "Tesla T4")'
# Expect: profile with calibrated_by: "auto"

# 8. Verify second instance skips calibration
# Start a second onnx engine (same GPU)
docker compose up -d stt-transcribe-onnx-2  # if configured
docker compose logs stt-transcribe-onnx-2 2>&1 | grep "calibrat"
# Expect: "vram_profile_found" (no calibration)
```

### Phase C (84.7–84.9)

```bash
# 9. Verify VRAM visibility in console
curl -s http://localhost:8000/api/console/engines \
    -H "Authorization: Bearer $DALSTON_API_KEY" | jq '.batch_engines[0].vram_profile_source'
# Expect: "calibrated"

# Verify VRAM detail endpoint
INSTANCE=$(curl -s http://localhost:8000/api/console/engines \
    -H "Authorization: Bearer $DALSTON_API_KEY" | jq -r '.batch_engines[0].instance')

curl -s "http://localhost:8000/api/console/engines/${INSTANCE}/vram" \
    -H "Authorization: Bearer $DALSTON_API_KEY" | jq '{budget_mb, profile_source, active_mode, solo_params, concurrent_params}'
# Expect: JSON with budget, params, coefficients

# 10. Verify runtime parameter override
curl -s -X POST "http://localhost:8000/api/console/engines/${INSTANCE}/vram/override" \
    -H "Authorization: Bearer $DALSTON_API_KEY" \
    -H "Content-Type: application/json" \
    -d '{"vad_batch_size_solo": 12}' | jq '{applied, estimated_peak_solo_mb, warnings}'
# Expect: applied=true, estimated_peak_solo_mb < gpu_vram_mb

# Verify override took effect (wait one heartbeat interval)
sleep 12
curl -s "http://localhost:8000/api/console/engines/${INSTANCE}/vram" \
    -H "Authorization: Bearer $DALSTON_API_KEY" | jq '.solo_params.vad_batch_size'
# Expect: 12

# 11. Verify VRAM suggestions
curl -s http://localhost:8000/api/console/vram-suggestions \
    -H "Authorization: Bearer $DALSTON_API_KEY" | jq '.suggestions'
# Expect: array (may be empty if engine hasn't processed enough tasks)

# 12. Verify web console shows VRAM panel
# Open http://localhost:8000/console/engines/<engine_id>
# Expect: VRAM Budget card with params, GPU usage bar, sparkline
```

---

## Checkpoint

### Phase A (84.1–84.4)

- [ ] `calibrate_vram.py` runs against ONNX transcribe engine and produces valid profile JSON
- [ ] `calibrate_vram.py` runs against pyannote diarize engine and produces valid profile JSON
- [ ] `VRAMBudget.compute_params()` returns sensible values for T4 (16 GB) and L4 (24 GB) budgets
- [ ] Diarize chunking splits a 2-hour file into ~8 chunks and produces correct merged output
- [ ] Speaker labels are consistent across chunk boundaries (same speaker gets same global label)
- [ ] Engine startup logs computed VRAM params when `DALSTON_VRAM_SHARE` is set
- [ ] Engine startup uses existing defaults when no VRAM budget is configured (backward compatible)
- [ ] Adaptive mode uses solo params (high batch) when queue depth ≤ 1 and concurrent params when queue depth > 1
- [ ] Colocated ONNX + pyannote on T4 with `VRAM_SHARE=0.45/0.55` processes 1-hour audio without OOM
- [ ] Ship calibration profiles for Parakeet TDT 0.6B v3 + pyannote 4.0 on T4 and L4

### Phase B (84.5–84.6)

- [ ] `vram_profiles` Postgres table created via migration
- [ ] `VRAMBudget.load()` queries central store before local files
- [ ] Existing local profiles auto-migrated to central store on first engine startup
- [ ] `compute_runtime_fingerprint()` produces stable, deterministic hashes
- [ ] Profile keyed by `(engine_id, model_id, gpu_model, runtime_fingerprint)` with unique constraint
- [ ] Console API: `GET /api/console/vram-profiles` returns stored profiles
- [ ] First engine on new hardware auto-calibrates and saves profile to central store
- [ ] Engine status shows "calibrating" during self-calibration, orchestrator skips it
- [ ] Self-calibration completes in under 5 minutes for transcribe engines
- [ ] Second engine on same hardware skips calibration and uses stored profile
- [ ] Stale fingerprint match: engine starts with old profile + schedules background re-calibration
- [ ] Auto-calibrated profiles use 20% safety margin (vs 15% for manual)

### Phase C (84.7–84.9)

- [ ] `EngineRecord` includes VRAM budget fields in heartbeat
- [ ] VRAM history ring buffer (30 min) written per heartbeat, readable via API
- [ ] `GET /api/console/engines/{instance}/vram` returns budget, params, coefficients, history
- [ ] Engine Detail page shows VRAM Budget panel with current params and solo/concurrent comparison
- [ ] Engine Detail page shows GPU usage bar with sparkline (last 30 min)
- [ ] Infrastructure page shows per-engine VRAM usage bars and over-commitment warnings
- [ ] `POST /api/console/engines/{instance}/vram/override` applies param changes at runtime
- [ ] Override rejected if estimated peak > 100% of GPU VRAM
- [ ] Override warning if estimated peak > 90% of GPU VRAM
- [ ] "Reset to Auto" clears overrides and recomputes from profile
- [ ] Manual overrides persist across container restarts (stored in `vram_profiles.manual_overrides`)
- [ ] VRAM advisor detects underutilisation (>30% headroom) and suggests increased batch size
- [ ] VRAM advisor detects OOM risk (<5% headroom) and suggests reduced batch size
- [ ] VRAM advisor detects profile drift (>10% divergence) and suggests re-calibration
- [ ] Suggestion banner shown on Engine Detail page with Apply/Dismiss actions
- [ ] Drift-triggered re-calibration runs automatically when engine is idle
