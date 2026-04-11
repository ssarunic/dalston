# M86: Shared VAD Chunking in BaseBatchTranscribeEngine

|                    |                                                              |
| ------------------ | ------------------------------------------------------------ |
| **Goal**           | Provide opt-in VAD-based audio chunking at the base engine level so any transcription engine can handle arbitrarily long audio |
| **Duration**       | 5–8 days                                                     |
| **Dependencies**   | None (uses existing Silero VAD model)                        |
| **Deliverable**    | SDK VAD utility, base engine integration, vLLM-ASR migration, ONNX migration path |
| **Status**         | Not Started                                                  |

## User Story

> *"As an engine developer, I want to declare `max_audio_duration_s = 30` on my engine class and have the SDK automatically split long audio into VAD-segmented chunks before calling `transcribe_audio()`, so that I don't need to implement chunking logic in every engine."*

---

## Outcomes

| Scenario | Current | After M86 |
| -------- | ------- | ---------- |
| Gemma 4 E4B via vLLM (30s audio limit) | Fails on any audio > 30s — model produces garbage or errors | Audio auto-chunked at speech boundaries, each chunk ≤ 30s, results merged into one Transcript |
| ONNX engine VAD chunking | Custom implementation in `onnx_inference.py` (~100 lines), not reusable | Delegates to shared `dalston.engine_sdk.vad` utility |
| New engine with audio length limit | Developer must copy VAD code from ONNX engine or implement from scratch | Set `max_audio_duration_s` on engine class, chunking is automatic |
| HF-ASR / NeMo (no limit) | Works on any length audio natively | No change — `max_audio_duration_s = None` (default), chunking skipped |
| faster-whisper | Uses library-internal Silero VAD | No change — keeps using faster-whisper's built-in VAD (better integrated with CTranslate2 batching) |

---

## Motivation

Audio LLMs (Gemma 4 E4B, future models) have hard limits on input audio duration imposed by their audio encoder architecture. Currently, the vLLM ASR engine sends full audio files with no chunking, making it incompatible with duration-limited models.

The ONNX engine already implements Silero VAD chunking, but it's buried inside `onnx_inference.py` and tightly coupled to the onnx-asr library's `load_vad()` function. No other engine can reuse it.

Lifting VAD chunking into `BaseBatchTranscribeEngine` provides:

1. **Gemma 4 E4B support** — the immediate need
2. **Future-proofing** — any new audio LLM with duration limits works automatically
3. **Consistency** — one VAD implementation, one set of tuning knobs
4. **ONNX migration path** — reduce ONNX engine complexity by delegating to the shared utility

---

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│              BaseBatchTranscribeEngine.process()              │
│                                                              │
│   audio_path ──► duration check                              │
│                    │                                         │
│         ┌─────────┴──────────┐                               │
│         │ ≤ max_duration     │ > max_duration                │
│         ▼                    ▼                               │
│   transcribe_audio()   VadChunker.split()                    │
│         │                    │                               │
│         │              ┌─────┴─────┐                         │
│         │              │ chunk_1   │ chunk_2  ... chunk_N    │
│         │              ▼           ▼                         │
│         │         transcribe_audio(chunk_request)  × N       │
│         │              │           │                         │
│         │              └─────┬─────┘                         │
│         │                    ▼                               │
│         │           _merge_chunk_transcripts()                │
│         ▼                    ▼                               │
│     TaskResponse(data=Transcript)                            │
└──────────────────────────────────────────────────────────────┘

VadChunker (dalston/engine_sdk/vad.py):
  ┌─────────────────────────────────────┐
  │  Silero VAD (ONNX, ~2 MB)          │
  │                                     │
  │  audio ──► speech segments          │
  │        ──► group into ≤ max_s chunks│
  │        ──► return (audio_slice,     │
  │             offset) pairs           │
  └─────────────────────────────────────┘
```

---

## Steps

### 86.1: Extract Silero VAD into SDK utility

**Files modified:**

- `dalston/engine_sdk/vad.py` *(new)* — shared VAD utility
- `dalston/engine_sdk/__init__.py` — export `VadChunker`

**Deliverables:**

A standalone, lazy-loading VAD utility that any engine can use without importing `onnx_asr`.

```python
# dalston/engine_sdk/vad.py

@dataclass
class SpeechSegment:
    """A detected speech region."""
    start: float   # seconds
    end: float     # seconds

@dataclass
class AudioChunk:
    """A chunk of audio for transcription."""
    audio_path: Path       # temp WAV file for this chunk
    offset: float          # start time in original audio (seconds)
    duration: float        # chunk duration (seconds)

class VadChunker:
    """Split audio into speech-bounded chunks using Silero VAD.

    Lazy-loads the Silero VAD ONNX model on first use. Thread-safe.
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
        exceed max_chunk_duration_s. Respects silence boundaries
        to avoid cutting mid-word.
        """
        ...
```

The Silero VAD model is loaded via `onnxruntime` (CPU-only, ~2 MB). If `DALSTON_SILERO_VAD_ONNX` env var is set (Docker images bake the model in), load from that path. Otherwise download from the silero-vad GitHub release.

**Tests:**

- Unit test with synthetic audio (silence + tone patterns)
- Test that chunks respect `max_chunk_duration_s`
- Test that speech segments are not split mid-segment
- Test lazy loading and caching of VAD model

---

### 86.2: Integrate VadChunker into BaseBatchTranscribeEngine

**Files modified:**

- `dalston/engine_sdk/base_transcribe.py` — add chunked processing path
- `dalston/engine_sdk/types.py` — add `audio_offset` to TaskRequest or a new ChunkContext

**Deliverables:**

Add opt-in VAD chunking to the base engine class. Engines declare their max audio duration; the base `process()` handles the rest.

```python
class BaseBatchTranscribeEngine(Engine):
    # Subclasses override to enable VAD chunking.
    # None = no limit (default, handles any length natively).
    max_audio_duration_s: float | None = None

    def process(self, task_request, ctx):
        if self._should_chunk(task_request):
            return self._process_chunked(task_request, ctx)
        transcript = self.transcribe_audio(task_request, ctx)
        return TaskResponse(data=transcript)

    def _should_chunk(self, task_request) -> bool:
        """Check if audio exceeds max_audio_duration_s."""
        if self.max_audio_duration_s is None:
            return False
        duration = get_audio_duration(task_request.audio_path)
        return duration > self.max_audio_duration_s

    def _process_chunked(self, task_request, ctx):
        """Split audio with VAD, transcribe each chunk, merge results."""
        chunker = VadChunker(max_chunk_duration_s=self.max_audio_duration_s)
        chunks = chunker.split(task_request.audio_path, temp_dir=ctx.temp_dir)

        transcripts = []
        for chunk in chunks:
            chunk_request = task_request.with_audio(chunk.audio_path)
            transcript = self.transcribe_audio(chunk_request, ctx)
            transcripts.append((transcript, chunk.offset))

        return TaskResponse(data=self._merge_chunk_transcripts(transcripts))

    def _merge_chunk_transcripts(self, chunks):
        """Merge chunk transcripts with offset-adjusted timestamps."""
        ...
```

Key design decisions:

- `max_audio_duration_s` is a **class attribute**, not a config parameter. The audio limit is a property of the model architecture, not a user preference.
- Chunking happens in `process()` before `transcribe_audio()` is called. The engine's `transcribe_audio()` always receives audio within its declared limit.
- Timestamps are adjusted by adding the chunk's `offset` to all segment/word timestamps.
- The merged transcript concatenates text with proper segment boundaries at chunk edges.

**Tests:**

- Test that `max_audio_duration_s = None` skips chunking (no behaviour change)
- Test that chunking triggers when audio exceeds limit
- Test timestamp offset adjustment in merged transcript
- Test that existing engines (HF-ASR, NeMo) are unaffected

---

### 86.3: Enable VAD chunking for vLLM-ASR engine

**Files modified:**

- `engines/stt-transcribe/vllm-asr/batch_engine.py` — add `max_audio_duration_s`

**Deliverables:**

The vLLM ASR engine declares its audio limit. For Gemma 4 E4B (30s), the base engine auto-chunks. For Voxtral/Qwen2 (no limit), chunking is skipped.

```python
class VllmAsrBatchEngine(BaseBatchTranscribeEngine):
    # Model-dependent. Gemma 4 E4B = 30s, Voxtral = None.
    # Read from engine config or model adapter at runtime.
    max_audio_duration_s: float | None = None

    def __init__(self, ...):
        super().__init__()
        # Set from model metadata or env var
        self.max_audio_duration_s = float(
            os.environ.get("DALSTON_MAX_AUDIO_DURATION_S", "0")
        ) or None
```

Alternatively, the adapter can declare the limit per model family:

```python
MODEL_AUDIO_LIMITS = {
    "google/gemma-4-E4B-it": 30.0,
    "google/gemma-4-E2B-it": 30.0,
    # Voxtral, Qwen2 — no limit (not in map)
}
```

And the engine sets `max_audio_duration_s` dynamically after model selection.

**Verification:**

```bash
# Transcribe 4-min audio with Gemma 4 E4B
export DALSTON_DEFAULT_MODEL=google/gemma-4-E4B-it
export DALSTON_MAX_AUDIO_DURATION_S=30
curl -X POST http://localhost:9100/v1/transcribe \
  -F "file=@long_audio.wav"
# Should succeed — auto-chunked into ~8 × 30s chunks
```

---

### 86.4: Migrate ONNX engine to shared VadChunker

**Files modified:**

- `dalston/engine_sdk/inference/onnx_inference.py` — replace inline VAD with `VadChunker`

**Deliverables:**

Replace the ONNX engine's custom `_transcribe_with_vad()` and `_get_or_load_vad()` methods with the shared `VadChunker`. This removes ~80 lines of ONNX-specific VAD code and the `onnx_asr.load_vad()` dependency.

Two migration options:

**Option A (clean):** Set `max_audio_duration_s = 60` on the ONNX batch engine and let `BaseBatchTranscribeEngine.process()` handle chunking. Remove `_transcribe_with_vad()` entirely.

**Option B (incremental):** Replace only the VAD loading and segmentation with `VadChunker` inside `_transcribe_with_vad()`, keeping the ONNX-specific batch inference loop. This preserves the per-segment batching (`vad_batch_size`) that the base engine doesn't handle.

**Recommendation:** Option B for this milestone. The ONNX engine's VAD batching (processing N segments in parallel on GPU) is a performance optimization that the base engine's sequential chunk processing doesn't replicate. Full migration to Option A requires adding batch support to `_process_chunked()`, which is out of scope.

**Tests:**

- Verify ONNX engine still produces identical output for existing test audio
- Verify `onnx_asr.load_vad()` is no longer called (shared Silero model used instead)

---

### 86.5: Enable VAD chunking for combo engine

**Files modified:**

- `engines/stt-transcribe/hf-asr-align-pyannote/engine.py` — use VadChunker for alignment pre-splitting

**Deliverables:**

Replace the combo engine's `_split_long_segments()` method (the one we built in this session that splits Whisper's single giant segment into ~30s chunks using word boundaries) with `VadChunker`. VAD-based splitting is more accurate than word-boundary splitting because it respects actual speech/silence boundaries in the audio, not just Whisper's attention-based timestamps.

---

## Non-Goals

- **Replacing faster-whisper's built-in VAD** — faster-whisper's Silero VAD is deeply integrated with CTranslate2's batched inference. Replacing it would regress performance. The shared `VadChunker` is for engines that don't have their own VAD.
- **Streaming/realtime VAD** — realtime engines already have per-utterance VAD via the realtime SDK's audio buffer. This milestone is batch-only.
- **Parallel chunk transcription** — chunks are transcribed sequentially. Parallel processing (multiple chunks on GPU simultaneously) is a future optimization, not needed for correctness.
- **VAD-based endpoint detection tuning** — the default Silero VAD parameters (threshold 0.5, min silence 0.3s) work well for speech. Per-engine tuning is deferred.

---

## Deployment

Standard rolling deploy. No migration required — the `max_audio_duration_s` attribute defaults to `None`, so existing engines are unaffected until they opt in.

The Silero VAD ONNX model (~2 MB) must be available at runtime. Options:

1. **Docker images** — bake into base images (already done for vLLM Dockerfile)
2. **Runtime download** — auto-download on first use from GitHub releases (fallback)
3. **S3 model storage** — load from S3 like other models (future)

---

## Verification

```bash
# 1. Verify vLLM-ASR with Gemma 4 E4B handles long audio
export DALSTON_DEFAULT_MODEL=google/gemma-4-E4B-it
export DALSTON_MAX_AUDIO_DURATION_S=30
python -m dalston.engine_sdk.local_runner run \
  --engine engines/stt-transcribe/vllm-asr/batch_engine.py:VllmAsrBatchEngine \
  --stage transcribe \
  --config '{"language": "en"}' \
  --audio ~/Downloads/audio-2.wav \
  --output /tmp/gemma4-output.json

# Verify output has properly offset timestamps
python -c "
import json
d = json.load(open('/tmp/gemma4-output.json'))['data']
segs = d['segments']
print(f'Segments: {len(segs)}')
print(f'Last segment end: {segs[-1][\"end\"]}s')
assert segs[-1]['end'] > 60, 'Timestamps should span full audio'
"

# 2. Verify ONNX engine still works after migration
python -m dalston.engine_sdk.local_runner run \
  --engine engines/stt-transcribe/onnx/batch_engine.py:OnnxBatchEngine \
  --stage transcribe \
  --config '{"model": "parakeet-onnx-tdt-0.6b-v3"}' \
  --audio ~/Downloads/audio-2.wav \
  --output /tmp/onnx-output.json

# 3. Verify engines without max_audio_duration_s are unaffected
python -c "
from dalston.engine_sdk.base_transcribe import BaseBatchTranscribeEngine
assert BaseBatchTranscribeEngine.max_audio_duration_s is None
print('Default is None — no chunking unless opted in')
"
```

---

## Checkpoint

- [ ] `dalston/engine_sdk/vad.py` — Silero VAD utility with `VadChunker.split()`
- [ ] `BaseBatchTranscribeEngine` — opt-in `max_audio_duration_s` with automatic chunking
- [ ] Chunk transcript merging with offset-adjusted timestamps
- [ ] vLLM-ASR engine declares `max_audio_duration_s` (Gemma 4 E4B = 30s)
- [ ] ONNX engine migrated to shared `VadChunker` (Option B — VAD loading only)
- [ ] Combo engine uses `VadChunker` instead of `_split_long_segments()`
- [ ] Unit tests for VadChunker, chunked processing, and timestamp merging
- [ ] Existing engines (HF-ASR, NeMo, faster-whisper) unaffected
