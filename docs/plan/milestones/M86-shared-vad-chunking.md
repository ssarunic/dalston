# M86: Shared VAD Chunking in BaseBatchTranscribeEngine

|                    |                                                              |
| ------------------ | ------------------------------------------------------------ |
| **Goal**           | Provide opt-in VAD-based audio chunking at the base engine level so any transcription engine can handle arbitrarily long audio |
| **Duration**       | 5–8 days                                                     |
| **Dependencies**   | None (uses existing Silero VAD model)                        |
| **Deliverable**    | SDK VAD utility, base engine integration, vLLM-ASR enablement, combo engine migration |
| **Status**         | Not Started                                                  |

## User Story

> *"As an engine developer, I want to declare a max audio duration on my engine and have the SDK automatically split long audio into VAD-segmented chunks before calling `transcribe_audio()`, so that I don't need to implement chunking logic in every engine."*

---

## Outcomes

| Scenario | Current | After M86 |
| -------- | ------- | ---------- |
| Gemma 4 E4B via vLLM (30s audio limit) | Fails on any audio > 30s — model produces garbage or errors | Audio auto-chunked at speech boundaries, each chunk ≤ 30s, results merged into one Transcript |
| New engine with audio length limit | Developer must implement VAD from scratch | Override `get_max_audio_duration_s()`, chunking is automatic |
| vLLM per-request model switching | N/A — no chunking | Limit resolved per-request from model metadata: Gemma = 30s, Voxtral = None |
| HF-ASR / NeMo / faster-whisper | Works on any length audio natively | No change — `get_max_audio_duration_s()` returns `None` (default) |

---

## Motivation

Audio LLMs (Gemma 4 E4B, future models) have hard limits on input audio duration imposed by their audio encoder architecture. Currently, the vLLM ASR engine sends full audio files with no chunking, making it incompatible with duration-limited models.

Lifting VAD chunking into `BaseBatchTranscribeEngine` provides:

1. **Gemma 4 E4B support** — the immediate need
2. **Future-proofing** — any new audio LLM with duration limits works automatically
3. **Consistency** — one VAD implementation, one set of tuning knobs

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
  sequentially. Parallel processing is a future optimization.
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
```

---

## Checkpoint

- [ ] `dalston/engine_sdk/vad.py` — `VadChunker` with `split()` and force-split fallback
- [ ] `TaskRequest.replace()` method for creating chunk requests
- [ ] `BaseBatchTranscribeEngine.get_max_audio_duration_s()` per-request hook
- [ ] `BaseBatchTranscribeEngine._process_chunked()` with timestamp-aware merge
- [ ] `_merge_chunk_transcripts()` with explicit field merge rules
- [ ] vLLM-ASR overrides `get_max_audio_duration_s()` with `MODEL_AUDIO_LIMITS` lookup
- [ ] Combo engine uses `VadChunker` instead of `_split_long_segments()`
- [ ] Unit tests for VadChunker, chunked processing, merge, and force-split
- [ ] Existing engines (HF-ASR, NeMo, faster-whisper, ONNX) unaffected
