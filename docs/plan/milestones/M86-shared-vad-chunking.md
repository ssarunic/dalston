# M86: Shared VAD Chunking in BaseBatchTranscribeEngine

|                    |                                                              |
| ------------------ | ------------------------------------------------------------ |
| **Goal**           | Opt-in base-engine VAD chunking for long audio               |
| **Duration**       | 5-8 days                                                     |
| **Dependencies**   | None (uses existing Silero VAD model)                        |
| **Deliverable**    | SDK VAD utility, base integration, NeMo opt-in + followups   |
| **Status**         | In progress; see steps 86.0-86.2, 86.5-86.7                  |

## User Story

> *"As an engine developer, I want to declare a max audio duration on my engine and have the SDK automatically split long audio into VAD-segmented chunks before calling `transcribe_audio()`, so that I don't need to implement chunking logic in every engine."*

---

## Outcomes

| Scenario | Current | After M86 |
| -------- | ------- | ---------- |
| Gemma 4 E4B via vLLM (30s audio limit) | Fails on any audio > 30s — model produces garbage or errors | Audio auto-chunked at speech boundaries, each chunk ≤ 30s, results merged into one Transcript |
| New engine with audio length limit | Developer must implement VAD from scratch | Override `get_max_audio_duration_s()`, chunking is automatic |
| vLLM per-request model switching | N/A — no chunking | Limit resolved per-request from model metadata: Gemma = 30s, Voxtral = None |
| HF-ASR / faster-whisper / ONNX | Works on any length (internal VAD or no ceiling) | No change — `get_max_audio_duration_s()` returns `None` (default) |
| NeMo Parakeet on L4 (22 GB) | OOMs at ~1h40m (linear activation ceiling even with local attention) | Auto-chunked at 1500s per chunk (`DALSTON_NEMO_MAX_CHUNK_S`), sequential, OOM-backoff on failure |

---

## Motivation

Two independent drivers motivated this milestone. A third surfaced during implementation.

**Driver 1 — Audio LLMs with hard duration caps.** Audio LLMs (Gemma 4 E4B, future models) have hard limits on input audio duration imposed by their audio encoder architecture. Currently, the vLLM ASR engine sends full audio files with no chunking, making it incompatible with duration-limited models.

**Driver 2 — Combo-engine word-boundary alignment.** The `hf-asr-align-pyannote` combo engine splits Whisper segments at word boundaries before passing them to wav2vec2 alignment. That's a hand-rolled splitter that would benefit from the same base-engine VAD utility.

**Driver 3 — NeMo L4 VRAM ceiling (discovered post-draft).** Parakeet with local attention fits any duration in principle, but on L4 (22 GB), linear activation growth exceeds available VRAM around ~100 minutes. Chunking is the only way to run >1h40m files on that hardware. NeMo was originally listed as "no change" in this milestone; the local-attention ceiling we discovered during OOM triage changed that, and NeMo is now the first engine to adopt the base chunker.

Lifting VAD chunking into `BaseBatchTranscribeEngine` provides:

1. **Gemma 4 E4B support** — original driver
2. **NeMo long-audio support on L4** — today's driver
3. **Future-proofing** — any new engine with duration limits works automatically
4. **Consistency** — one VAD implementation, one set of tuning knobs

---

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│              BaseBatchTranscribeEngine.process()              │
│                                                              │
│   task_request ──► get_max_audio_duration_s(task_request)    │
│                      │                                       │
│           ┌──────────┴───────────┐                           │
│           │ None or within limit │ exceeds limit             │
│           ▼                      ▼                           │
│     transcribe_audio()     VadChunker.split()                │
│           │                      │                           │
│           │              ┌───────┴───────┐                   │
│           │              │ chunk_1       │ chunk_N           │
│           │              ▼               ▼                   │
│           │         transcribe_audio(chunk_req) × N          │
│           │              │               │                   │
│           │              └───────┬───────┘                   │
│           │                      ▼                           │
│           │         _merge_chunk_transcripts()                │
│           ▼                      ▼                           │
│       TaskResponse(data=Transcript)                          │
└──────────────────────────────────────────────────────────────┘

VadChunker (dalston/engine_sdk/vad.py):
  ┌─────────────────────────────────────┐
  │  Silero VAD (torch-based, CPU)      │
  │                                     │
  │  audio ──► speech segments          │
  │        ──► group into ≤ max_s chunks│
  │        ──► force-split overlong     │
  │             segments at max_s       │
  │        ──► return AudioChunk list   │
  └─────────────────────────────────────┘
```

---

## Steps

### 86.0: Fix multi-input result parsing in `NemoInference`

**Files modified:**

- `dalston/engine_sdk/inference/nemo_inference.py` — split `transcribe_with_model` into a single-input wrapper plus a new list-in/list-out `transcribe_batch_with_model`
- `tests/unit/test_parakeet_batch_contract.py` — two new unit tests covering list-in/list-out

**Motivation:**

`transcribe_with_model()` currently accepts `audio: str | np.ndarray | list` but only parses `transcriptions[0]`, silently discarding results 1..N when given a list. Nothing in the codebase passes a list today, so the bug has never fired — but the type signature invites it and any future parallel-batch optimisation would hit it.

**Fix shape:**

```python
def transcribe_with_model(
    self, model, audio: str | np.ndarray, batch_size=None,
) -> NeMoTranscriptionResult:
    results = self.transcribe_batch_with_model(model, [audio], batch_size=batch_size)
    return results[0] if results else NeMoTranscriptionResult()

def transcribe_batch_with_model(
    self, model, audio_list: list, batch_size=None,
) -> list[NeMoTranscriptionResult]:
    # Iterate over transcriptions[0..N-1], parse each hypothesis,
    # return a list of NeMoTranscriptionResult in input order.
```

Existing callers ([`nemo/batch_engine.py`](../../../engines/stt-transcribe/nemo/batch_engine.py), [`nemo_inference.py::_run_streaming_inference`](../../../dalston/engine_sdk/inference/nemo_inference.py)) pass single items, so no call-site changes needed. The `list` type drops off the single-input signature.

**Telemetry:** one `engine.recognize` span per call, `dalston.batch_size=N`, `dalston.audio_duration_s` = sum across inputs. Same contract M86.6 uses for the aggregate chunked span.

**Tests:**

- `test_transcribe_batch_returns_one_result_per_input` — mock `model.transcribe` to return N hypotheses, call `transcribe_batch_with_model` with N items, assert `len(result) == N` and each carries its own text.
- `test_transcribe_with_model_single_wraps_batch` — assert the single-input method delegates to the batch path and unwraps correctly.

**Note:** this step *unblocks* a future parallel-batch optimisation but does not enable it. Sequential chunked transcription remains the default in 86.2. Parallel batch stays a non-goal of this milestone.

---

### 86.1: Extract Silero VAD into SDK utility

**Files modified:**

- `dalston/engine_sdk/vad.py` *(new)* — shared VAD utility
- `dalston/engine_sdk/__init__.py` — export `VadChunker`

**Deliverables:**

A standalone, lazy-loading VAD utility that any engine can use.

```python
# dalston/engine_sdk/vad.py

@dataclass
class SpeechSegment:
    """A detected speech region."""
    start: float   # seconds
    end: float     # seconds

@dataclass
class AudioChunk:
    """A chunk of audio ready for transcription."""
    audio_path: Path       # temp WAV file for this chunk
    offset: float          # start time in original audio (seconds)
    duration: float        # chunk duration (seconds)

class VadChunker:
    """Split audio into speech-bounded chunks using Silero VAD.

    Lazy-loads the Silero VAD model on first use. Thread-safe.

    Invariant: every returned chunk has duration ≤ max_chunk_duration_s.
    When a single speech span exceeds the limit (no internal silence),
    the chunker force-splits at max_chunk_duration_s boundaries and
    logs a warning. This produces imperfect cuts but prevents model
    failures on duration-limited audio encoders.
    """

    def __init__(
        self,
        max_chunk_duration_s: float = 30.0,
        min_speech_duration_s: float = 0.25,
        min_silence_duration_s: float = 0.3,
        vad_threshold: float = 0.5,
    ) -> None: ...

    def detect_speech(self, audio_path: Path) -> list[SpeechSegment]:
        """Run VAD on audio file and return speech regions."""
        ...

    def split(
        self, audio_path: Path, temp_dir: Path | None = None
    ) -> list[AudioChunk]:
        """Split audio into chunks at speech boundaries.

        Groups consecutive speech segments into chunks that don't
        exceed max_chunk_duration_s. When a single speech segment
        exceeds the limit (continuous speech without silence), it is
        force-split at the boundary with a warning logged.

        Returns AudioChunk list with temp WAV files and offsets.
        """
        ...
```

**VAD backend:** Use `torch.hub.load("snakers4/silero-vad")` which is
pure PyTorch (no `onnxruntime` dependency). This avoids packaging
conflicts — every engine that imports `torch` already has what it needs.
For Docker images that bake the model, honour `DALSTON_SILERO_VAD_PATH`
env var to skip the download.

**Force-split policy:** When a speech region exceeds `max_chunk_duration_s`
and contains no internal silence, split at the limit boundary. The last
chunk may be shorter. Log a warning with the segment duration so operators
can tune VAD sensitivity if it happens often.

**Tests:**

- Unit test with synthetic audio (silence + tone patterns)
- Test that chunks respect `max_chunk_duration_s` hard limit
- Test force-split on continuous speech (no silence)
- Test lazy loading and caching of VAD model

---

### 86.2: Integrate VadChunker into BaseBatchTranscribeEngine

**Files modified:**

- `dalston/engine_sdk/base_transcribe.py` — add chunked processing path
- `dalston/engine_sdk/types.py` — add `replace()` method to `TaskRequest`

**Deliverables:**

Add opt-in VAD chunking to the base engine class. The audio duration
limit is resolved **per request** via a method that engines override.

```python
class BaseBatchTranscribeEngine(Engine):

    def get_max_audio_duration_s(
        self, task_request: TaskRequest
    ) -> float | None:
        """Return the max audio duration this engine can handle for the
        given request, or None if there is no limit.

        Subclasses override this. The default returns None (no chunking).
        vLLM overrides to look up the limit from model metadata.
        """
        return None

    def process(self, task_request, ctx):
        max_s = self.get_max_audio_duration_s(task_request)
        if max_s is not None and self._audio_exceeds(task_request, max_s):
            return self._process_chunked(task_request, ctx, max_s)
        transcript = self.transcribe_audio(task_request, ctx)
        return TaskResponse(data=transcript)

    def _process_chunked(self, task_request, ctx, max_s):
        chunker = VadChunker(max_chunk_duration_s=max_s)
        chunks = chunker.split(task_request.audio_path, temp_dir=ctx.temp_dir)

        chunk_results: list[tuple[Transcript, float]] = []
        for chunk in chunks:
            chunk_request = task_request.replace(audio_path=chunk.audio_path)
            transcript = self.transcribe_audio(chunk_request, ctx)
            chunk_results.append((transcript, chunk.offset))

        return TaskResponse(
            data=self._merge_chunk_transcripts(chunk_results)
        )
```

**`TaskRequest.replace()`:** New method that returns a shallow copy with
specified fields overridden. Simpler than a full builder pattern:

```python
@dataclass
class TaskRequest:
    ...
    def replace(self, **kwargs) -> TaskRequest:
        """Return a copy with the given fields replaced."""
        import dataclasses
        return dataclasses.replace(self, **kwargs)
```

**`_merge_chunk_transcripts()` contract:** Merges N chunk transcripts
into one canonical `Transcript`. Explicit rules:

| Field | Merge strategy |
| ----- | -------------- |
| `text` | Concatenate with space separator |
| `segments` | Concatenate; adjust `start`/`end` by adding chunk offset |
| `segments[*].words` | Adjust `start`/`end` by chunk offset |
| `language` | Use first chunk's language (all chunks share the same audio language) |
| `language_confidence` | Average across chunks |
| `alignment_method` | Use first chunk's value (same engine, same method) |
| `engine_id` | Use first chunk's value |
| `warnings` | Concatenate and deduplicate |
| `metadata` | Not merged — per-segment metadata stays with its segment |

**Tests:**

- Test that `get_max_audio_duration_s` returning `None` skips chunking
- Test that chunking triggers when audio exceeds limit
- Test timestamp offset adjustment in merged segments and words
- Test text concatenation across chunks
- Test `TaskRequest.replace()` produces independent copy
- Test existing engines (HF-ASR, NeMo) are unaffected

---

### 86.3: Enable VAD chunking for vLLM-ASR engine

**Files modified:**

- `engines/stt-transcribe/vllm-asr/batch_engine.py` — override `get_max_audio_duration_s()`

**Deliverables:**

The vLLM ASR engine resolves the audio limit **per request** based on
which model is selected. This handles per-request model switching
correctly — a Voxtral request has no limit, a Gemma 4 E4B request
gets 30s.

```python
# Model-specific audio duration limits (seconds).
# Models not listed have no limit.
MODEL_AUDIO_LIMITS: dict[str, float] = {
    "google/gemma-4-E4B-it": 30.0,
    "google/gemma-4-E2B-it": 30.0,
}

class VllmAsrBatchEngine(BaseBatchTranscribeEngine):

    def get_max_audio_duration_s(
        self, task_request: TaskRequest
    ) -> float | None:
        params = task_request.get_transcribe_params()
        model_id = params.loaded_model_id or self._default_model_id
        return MODEL_AUDIO_LIMITS.get(model_id)
```

Also supports env-var override for unlisted models:
`DALSTON_MAX_AUDIO_DURATION_S=30` forces chunking for any model.

**Verification:**

```bash
# 4-min audio with Gemma 4 E4B — auto-chunked into ~8 × 30s
export DALSTON_DEFAULT_MODEL=google/gemma-4-E4B-it
python -m dalston.engine_sdk.local_runner run \
  --engine engines/stt-transcribe/vllm-asr/batch_engine.py:VllmAsrBatchEngine \
  --stage transcribe --config '{"language": "en"}' \
  --audio ~/Downloads/audio-2.wav --output /tmp/gemma4.json
python -c "
import json; d=json.load(open('/tmp/gemma4.json'))['data']
print(f'Segments: {len(d[\"segments\"])}')
assert d['segments'][-1]['end'] > 60, 'Timestamps should span full audio'
"
```

---

### 86.4: Migrate combo engine to VadChunker

**Files modified:**

- `engines/stt-transcribe/hf-asr-align-pyannote/engine.py` — replace
  `_split_long_segments()` with `VadChunker`

**Deliverables:**

Replace the combo engine's word-boundary splitting (built earlier in
this session) with proper VAD-based splitting. VAD splitting is more
accurate because it uses actual speech/silence boundaries in the audio
rather than Whisper's attention-based word timestamps.

The combo engine's `_run_align()` currently calls
`_split_long_segments()` to break Whisper's single segment into ~30s
chunks for wav2vec2 alignment. Replace with:

```python
chunker = VadChunker(max_chunk_duration_s=30.0)
speech_segments = chunker.detect_speech(audio_path)
# Build InputSegment list from speech regions instead of word boundaries
```

---

### 86.5: OOM backoff in `_process_chunked`

**Files modified:**

- `dalston/engine_sdk/base_transcribe.py` — wrap per-chunk `transcribe_audio()` calls in an OOM-aware retry loop

**Motivation:**

A fixed `max_chunk_s` cannot anticipate every runtime pressure situation: two replicas sharing an L4, a fresh model loaded mid-job, a larger model variant swapped in at runtime. ONNX ([`onnx_inference.py:271-294`](../../../dalston/engine_sdk/inference/onnx_inference.py#L271-L294)) and faster-whisper ([`faster_whisper_inference.py:303`](../../../dalston/engine_sdk/inference/faster_whisper_inference.py#L303)) both already have runtime OOM backoff. M86 needs the same safety net or a reasonable default chunk size becomes a brittle tuning problem.

**Behaviour:**

On `CUDA out of memory` from a chunk's `transcribe_audio()`:

1. Log `chunked_oom_backoff` with old/new chunk sizes.
2. Halve the effective `max_chunk_s` for the engine instance (cached so subsequent tasks skip failed sizes).
3. Re-split the *remaining* audio (not the whole file — already-successful chunks stay done).
4. Retry.
5. Floor at 60s — below that, raise rather than slice into confetti.

Uses the existing `dalston.engine_sdk.inference.gpu_guard.is_oom_error` helper so the detection logic is shared with ONNX / faster-whisper.

**Tests:**

- Monkeypatch `transcribe_audio` to raise CUDA OOM once, assert backoff halves `max_chunk_s` and the retry succeeds.
- Monkeypatch to OOM repeatedly until floor, assert a loud failure once the floor is hit.

---

### 86.6: Aggregate telemetry for chunked requests

**Files modified:**

- `dalston/engine_sdk/base_transcribe.py` — wrap the whole chunked run in a single top-level span
- `dalston/telemetry.py` (if span helpers live there) — minor attribute additions

**Motivation:**

Naively calling `transcribe_audio()` N times per chunked request would emit N `engine.recognize` spans and N `engine_recognize_seconds` metric observations in Prometheus. In observability, one chunked job would look like N separate requests, skewing latency dashboards and RTF calculations.

**Contract:**

- Top-level span `engine.recognize` with attributes:
  - `dalston.chunked=true`
  - `dalston.chunk_count=N`
  - `dalston.chunk_max_s=<effective_max_s>`
  - `dalston.audio_duration_s=<total_s>` (full audio, not per-chunk sum)
- Per-chunk work lives in child spans named `engine.chunk_recognize` with `dalston.chunk_index` and `dalston.chunk_duration_s` attributes.
- `engine_recognize_seconds` / `engine_realtime_factor` metrics emit **once** at aggregate level with the total wall time and total audio duration.

Non-chunked requests keep their current single-span, single-metric contract untouched.

---

### 86.7: NeMo opt-in

**Files modified:**

- `engines/stt-transcribe/nemo/batch_engine.py` — override `get_max_audio_duration_s`

**Behaviour:**

```python
def get_max_audio_duration_s(self, task_request: TaskRequest) -> float | None:
    return float(os.environ.get("DALSTON_NEMO_MAX_CHUNK_S", "1500"))
```

**Rationale for default 1500s:**

Measured on 2026-04-15 on an L4 (22 GB) running `parakeet-tdt-0.6b-v3` with local attention:

| audio_s | peak VRAM |
| ------: | --------: |
| 60      | 8262 MB   |
| 600     | 8262 MB   |
| 1200    | 10162 MB  |
| 1800    | 12182 MB  |

At 1500s per chunk, peak ≈ 11 GB on L4 — half of the 22 GB budget. Leaves real headroom for co-located replicas and the OOM-backoff safety net from 86.5. Override with `DALSTON_NEMO_MAX_CHUNK_S` on bigger (A100: try 3000+) or smaller GPUs.

**Short-file fast path:** audio ≤ 1500s skips chunking entirely and behaves identically to today.

---

## Non-Goals

- **ONNX engine migration** — The ONNX engine uses `onnx-asr`'s
  `model.with_vad()` which feeds VAD segments directly into the model's
  batch inference pipeline. This is a fundamentally different API than
  file-slice chunking and cannot be replaced without a performance
  regression. ONNX keeps its own VAD.
- **faster-whisper migration** — faster-whisper's Silero VAD is deeply
  integrated with CTranslate2's batched inference. No benefit to
  replacing it.
- **Streaming/realtime VAD** — realtime engines already have
  per-utterance VAD via the realtime SDK's audio buffer. M86 is
  batch-only.
- **Parallel chunk transcription** — chunks are transcribed
  sequentially. Step 86.0 removes the silent-data-loss trap in
  `NemoInference.transcribe_with_model`, which technically *unblocks*
  parallel batch as a future optimisation, but enabling it is explicitly
  out of scope here. Sequential is the safer default for today's
  capacity-focused change.
- **Tightening advertised `max_audio_duration`** — model YAMLs still
  advertise `max_audio_duration: 7200` (or higher) for Parakeet and
  similar engines. Post-chunking, that claim becomes truthful (or could
  even be raised to `null`/unlimited). Ops-level model-catalog change;
  out of scope for this PR.
- **Gateway model routing for Gemma** — adding Gemma 4 to the gateway's
  model resolver / HF routing table is a separate concern. M86 is
  SDK-level chunking only.

---

## Deployment

Standard rolling deploy. No migration required — `get_max_audio_duration_s()`
defaults to `None`, so existing engines are unaffected until they opt in.

The Silero VAD model is loaded via `torch.hub` (pure PyTorch, no
`onnxruntime` dependency). Every engine that imports `torch` already has
what it needs. Docker images that bake the model set
`DALSTON_SILERO_VAD_PATH` to skip the runtime download.

**NeMo defaults.** `DALSTON_NEMO_MAX_CHUNK_S=1500`. Override per
deployment if the GPU tier changes (A100: try 3000+; T4: try 900). The
OOM-backoff loop from 86.5 protects against under-tuning in either
direction — a misconfigured value gets halved on first OOM and cached,
so the next task runs at the proven safe size.

---

## Verification

```bash
# 1. vLLM-ASR with Gemma 4 E4B handles long audio
export DALSTON_DEFAULT_MODEL=google/gemma-4-E4B-it
python -m dalston.engine_sdk.local_runner run \
  --engine engines/stt-transcribe/vllm-asr/batch_engine.py:VllmAsrBatchEngine \
  --stage transcribe \
  --config '{"language": "en"}' \
  --audio ~/Downloads/audio-2.wav \
  --output /tmp/gemma4-output.json

python -c "
import json
d = json.load(open('/tmp/gemma4-output.json'))['data']
segs = d['segments']
print(f'Segments: {len(segs)}, last end: {segs[-1][\"end\"]}s')
assert segs[-1]['end'] > 60, 'Timestamps should span full audio'
"

# 2. Voxtral (no limit) still works without chunking
export DALSTON_DEFAULT_MODEL=mistralai/Voxtral-Mini-3B-2507
python -m dalston.engine_sdk.local_runner run \
  --engine engines/stt-transcribe/vllm-asr/batch_engine.py:VllmAsrBatchEngine \
  --stage transcribe \
  --config '{"language": "en"}' \
  --audio ~/Downloads/audio-2.wav \
  --output /tmp/voxtral-output.json

# 3. Default base engine has no limit
python -c "
from dalston.engine_sdk.base_transcribe import BaseBatchTranscribeEngine
e = BaseBatchTranscribeEngine.__new__(BaseBatchTranscribeEngine)
assert e.get_max_audio_duration_s(None) is None
print('Default: no chunking')
"

# 4. NeMo on L4 handles a 34-min file via chunking without OOM
curl -F "file=@/tmp/original.mp3" -F "model=parakeet-tdt-0.6b-v3" \
  http://<nemo-worker>:9100/v1/transcribe | \
  jq '.segments | length, .[-1].end'
# Expected: ~2-3 chunks, segments[-1].end >= 2050 (full 34-min preserved)

# 5. NeMo chunked request reports as a single engine.recognize span
# Check Jaeger / OTLP backend for the chunked trace:
#   span engine.recognize attributes:
#     dalston.chunked = true
#     dalston.chunk_count = 2 or 3
#     dalston.audio_duration_s ≈ 2057
# Plus child spans engine.chunk_recognize for each chunk.
```

---

## Checkpoint

- [ ] **86.0** `NemoInference.transcribe_batch_with_model` + single-input wrapper
- [ ] **86.0** Unit tests for list-in/list-out result parsing
- [ ] **86.1** `dalston/engine_sdk/vad.py` — `VadChunker` with `split()` and force-split fallback
- [ ] **86.2** `TaskRequest.replace()` method for creating chunk requests
- [ ] **86.2** `BaseBatchTranscribeEngine.get_max_audio_duration_s()` per-request hook
- [ ] **86.2** `BaseBatchTranscribeEngine._process_chunked()` with timestamp-aware merge
- [ ] **86.2** `_merge_chunk_transcripts()` with explicit field merge rules
- [ ] **86.5** OOM backoff loop in `_process_chunked` (halve on OOM, floor at 60s)
- [ ] **86.6** Aggregate `engine.recognize` span + single metric emission per chunked request
- [ ] **86.7** `NemoBatchEngine.get_max_audio_duration_s()` override with `DALSTON_NEMO_MAX_CHUNK_S=1500` default
- [ ] **86.3** *(deferred)* vLLM-ASR overrides `get_max_audio_duration_s()` with `MODEL_AUDIO_LIMITS` lookup
- [ ] **86.4** *(deferred)* Combo engine uses `VadChunker` instead of `_split_long_segments()`
- [ ] Unit tests for VadChunker, chunked processing, merge, force-split, and OOM backoff
- [ ] Existing engines (HF-ASR, faster-whisper, ONNX, combo) unaffected
- [ ] 34-min L4 integration test (NeMo) passes end-to-end on the running `dalston-gpu-nemo` worker
