# M84: VRAM Budget Management & Diarization Chunking

|                    |                                                              |
| ------------------ | ------------------------------------------------------------ |
| **Goal**           | Engines auto-tune parameters to maximise throughput within a VRAM budget without OOM |
| **Duration**       | 8–12 days                                                    |
| **Dependencies**   | M37 (Capacity Management), M76 (Engine Telemetry Depth)      |
| **Deliverable**    | Calibration script, VRAM profiles, runtime budget calculator, diarization chunking, engine startup integration |
| **Status**         | Not Started                                                  |

## User Story

> *"As an operator deploying two GPU engines on a single instance, I want each engine to automatically compute its optimal parameters (batch size, concurrency, chunk duration) from its VRAM allocation, so that I get maximum throughput without OOM crashes or manual tuning."*

---

## Outcomes

| Scenario | Current | After M84 |
| -------- | ------- | ---------- |
| 2-hour audio diarization on T4 (16 GB) | pyannote 4.0 OOMs at ~28 min (reconstruction spike) | Audio chunked into 15-min segments, diarized independently, speaker labels merged — completes successfully |
| ONNX + pyannote colocated on g6.xlarge (24 GB) | No VRAM coordination — either engine can grab all VRAM, OOM depends on job ordering | Each engine has a VRAM budget (e.g. 10 GB / 12 GB), parameters auto-tuned to fit, concurrent operation safe |
| Operator deploys on new GPU (A10, L4, T4) | Must manually tune `DALSTON_VAD_BATCH_SIZE`, `DALSTON_VAD_MAX_SPEECH_S` per GPU | Set `DALSTON_VRAM_BUDGET_MB=10000`, engine reads calibration profile and computes optimal params |
| Batch concurrency on transcribe engine | `DALSTON_BATCH_MAX_INFLIGHT=4` is a guess, may OOM with large files | Inflight limit derived from VRAM budget: `(budget - weights) / per_request_activation` |
| Single file in queue, GPU underutilised | `vad_batch_size=1` always, GPU mostly idle between small inference calls | Engine detects shallow queue, switches to solo mode (high batch_size) for full GPU utilisation; switches back to concurrent mode when queue fills |

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

## Non-Goals

- **ONNX `gpu_mem_limit` enforcement** — The calibrator tells the engine what params are safe; we don't need hard VRAM caps via ONNX session options. Hard caps cause cryptic failures; computed params prevent overuse gracefully.
- **Dynamic VRAM rebalancing** — Engines get a static budget at startup. Dynamic rebalancing (idle engine yields VRAM) is a separate concern and requires model CPU offloading (future milestone).
- **Multi-GPU partitioning** — This milestone assumes single-GPU instances. MIG/MPS partitioning is out of scope.
- **Calibrating all engine × model × GPU combinations** — Ship profiles for the primary deployment target (Parakeet TDT 0.6B v3 ONNX + pyannote 4.0 on T4 and L4). Other combinations use conservative defaults until calibrated.
- **Cross-engine VRAM coordination at runtime** — No runtime protocol between containers. Static budget split at deploy time is sufficient.

---

## Deployment

### Ordering

1. **84.1** (calibration script) can be built and run independently
2. **84.2** (budget calculator) depends on 84.1's output format
3. **84.3** (diarize chunking) is independent of 84.1/84.2 — can be built in parallel
4. **84.4** (startup integration) depends on 84.2 and 84.3

### Migration

- All changes are **additive**. Without `DALSTON_VRAM_BUDGET_MB` or `DALSTON_VRAM_SHARE`, engines behave exactly as today.
- Diarize chunking activates only when audio exceeds `DALSTON_MAX_DIARIZE_CHUNK_S` (default 900s = 15 min). Files under 15 min take the existing path.
- Ship initial calibration profiles for T4 and L4 alongside the code. Operators can re-calibrate for their hardware.

---

## Verification

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
# Generate 30-min test audio
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

---

## Checkpoint

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
