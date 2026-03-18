# M81: Audio Preparation as SDK Utility

|                    |                                                                                              |
| ------------------ | -------------------------------------------------------------------------------------------- |
| **Goal**           | Move audio format conversion into the engine SDK so engines are self-sufficient, while keeping the prepare stage as a pipeline optimization |
| **Duration**       | 3–5 days                                                                                     |
| **Dependencies**   | None                                                                                         |
| **Deliverable**    | `ensure_audio_format()` SDK utility, engines calling it defensively, prepare stage as optimization layer |
| **Status**         | Not Started                                                                                  |

## User Story

> *"As an engine developer, I want my engine to work correctly regardless of whether the prepare stage ran before it — so I can test engines in isolation, run them outside the pipeline, and not worry about implicit format contracts breaking silently."*

---

## Motivation

The prepare stage converts any input audio to 16kHz, mono, 16-bit PCM WAV. All downstream engines (transcribe, diarize, align) declare this format in their `engine.yaml` and trust it blindly — no validation, no fallback. This creates three problems:

1. **Engines are not portable.** You cannot run a transcription engine standalone or in a test harness without first producing a correctly formatted WAV. Every engine test must either mock audio I/O or run the full pipeline.

2. **Implicit format contract.** The agreement between prepare and downstream engines exists only as matching `engine.yaml` declarations and a hardcoded ffmpeg command in `engines/stt-prepare/audio-prepare/engine.py`. Nothing enforces it at runtime. If the prepare stage output changes (intentionally or via a bug), downstream engines fail in unpredictable ways.

3. **All-or-nothing coupling.** If a future engine needs a different format (e.g., 8kHz for telephony, 22.05kHz for a music-aware model), the prepare stage must change to produce multiple outputs — or the new engine must break the contract and handle conversion itself as a one-off.

Today all speech models converge on 16kHz mono, so these problems are latent rather than acute. But the fix is cheap and makes the system strictly more robust: engines declare what they need, the SDK guarantees it, and the prepare stage remains as a performance optimization (convert once) rather than a correctness requirement.

---

## Architecture

```
BEFORE (prepare is a correctness requirement):

  Any Format ──▶ [prepare] ──▶ 16kHz WAV ──▶ [transcribe]  (trusts blindly)
                                         ├──▶ [diarize]     (trusts blindly)
                                         └──▶ [align]       (trusts blindly)

AFTER (prepare is a performance optimization):

  Any Format ──▶ [prepare] ──▶ 16kHz WAV ──▶ [transcribe]  ──▶ ensure_audio_format() ──▶ no-op ✓
                                         ├──▶ [diarize]     ──▶ ensure_audio_format() ──▶ no-op ✓
                                         └──▶ [align]       ──▶ ensure_audio_format() ──▶ no-op ✓

  Already-correct WAV ──▶ [transcribe] ──▶ ensure_audio_format() ──▶ no-op ✓   (no prepare needed)
  Raw MP3 ──▶ [transcribe] ──▶ ensure_audio_format() ──▶ converts via ffmpeg   (standalone mode)
```

The key invariant: **after this milestone, removing the prepare stage from the DAG produces correct (but slower) results for single-channel input.** For `per_channel` mode (stereo channel splitting), prepare remains a hard requirement — see Non-Goals. For all other cases, the prepare stage earns its place through efficiency, not necessity.

---

## Steps

### 81.1: `ensure_audio_format()` SDK Utility

**Files modified:**

- `dalston/engine_sdk/audio.py` *(new)*
- `dalston/engine_sdk/__init__.py`

**Deliverables:**

A single utility function that checks whether audio is already in the target format and converts only if needed.

```python
@dataclass(frozen=True)
class AudioFormat:
    """Declares an engine's audio input requirements."""
    sample_rate: int = 16000
    channels: int = 1
    bit_depth: int = 16

SPEECH_STANDARD = AudioFormat()  # 16kHz, mono, 16-bit — the common case


def ensure_audio_format(
    audio_path: Path,
    target: AudioFormat = SPEECH_STANDARD,
    work_dir: Path | None = None,
) -> Path:
    """Ensure audio file matches the target format.

    If the file is already compliant, returns the original path (no copy,
    no conversion). Otherwise, converts via ffmpeg into work_dir and
    returns the path to the converted file.

    Raises EngineAudioError if ffmpeg is not available or conversion fails.
    """
```

Implementation details:

- **Format detection**: Read WAV header via `soundfile.info()` (already available in engine containers). Check sample rate, channels, subtype. This is a metadata read — no audio decoding, effectively free.
- **Fast path**: If all properties match, return the input path immediately. This is the expected path in production (prepare already ran).
- **Slow path**: Shell out to ffmpeg with the same flags the prepare engine uses: `-ar {target.sample_rate} -ac {target.channels} -sample_fmt s16 -f wav`. Write to `work_dir / "prepared_{hash}.wav"` where hash is derived from the input filename (avoids collisions if called multiple times).
- **Non-WAV input**: `soundfile.info()` works for WAV, FLAC, OGG. For MP3, M4A, and container formats (MP4, MKV), it raises an error. In that case, always take the slow path — the file needs conversion regardless.
- **ffmpeg availability**: Check once at import time via `shutil.which("ffmpeg")`. If absent and the slow path is needed, raise `EngineAudioError` with a clear message. Do not fail on import if ffmpeg is missing — the fast path doesn't need it.

```python
class EngineAudioError(Exception):
    """Raised when audio cannot be prepared for engine consumption."""
```

**Tests:**

- WAV already at 16kHz/mono/s16 → returns same path, no subprocess call
- WAV at 44.1kHz stereo → converts, returns new path, verify format of output
- MP3 input → converts, returns WAV
- Missing ffmpeg + non-compliant input → raises `EngineAudioError`
- Missing ffmpeg + compliant WAV → returns same path (no error)

---

### 81.2: Integrate into Engine Base Class

**Files modified:**

- `dalston/engine_sdk/base.py`
- `dalston/engine_sdk/types.py`

**Deliverables:**

Add an optional `audio_format` class attribute to `Engine` and wire `ensure_audio_format()` into the task lifecycle so engines get it for free.

```python
class Engine:
    """Base class for batch processing engines."""

    # Override in subclass to declare audio requirements.
    # Set to None for engines that don't consume audio (e.g., merge, llm-cleanup).
    audio_format: AudioFormat | None = SPEECH_STANDARD

    ...
```

In `TaskRequest.__post_init__` (or in `runner.py` after materializing artifacts but before calling `engine.process()`), add:

```python
if engine.audio_format is not None and task_request.audio_path is not None:
    task_request.audio_path = ensure_audio_format(
        task_request.audio_path,
        target=engine.audio_format,
        work_dir=task_work_dir,
    )
```

This means:

- Engines that don't override `audio_format` get 16kHz/mono/16-bit validation for free
- Engines that need a different format override: `audio_format = AudioFormat(sample_rate=8000)`
- Engines that don't consume audio (merge, llm-cleanup) set: `audio_format = None`

The runner logs a structured message at DEBUG level when conversion happens (indicating prepare either didn't run or produced a different format) and at TRACE level when the fast path is taken.

---

### 81.3: Update Existing Engines

**Files modified:**

- `engines/stt-transcribe/faster-whisper/batch_engine.py`
- `engines/stt-transcribe/onnx/batch_engine.py`
- `engines/stt-diarize/pyannote-4.0/engine.py`
- `engines/stt-diarize/nemo-msdd/engine.py`
- `engines/stt-align/phoneme-align/engine.py`
- `engines/stt-merge/final-merger/engine.py`
- `engines/stt-prepare/audio-prepare/engine.py`

**Deliverables:**

Minimal changes per engine:

- **Transcribe, diarize engines**: No code changes needed. They inherit `audio_format = SPEECH_STANDARD` from `Engine` base class. The runner handles conversion automatically.
- **Alignment engine**: Remove the internal `_load_audio` resampling fallback in `engine.py:228-242`. It is redundant now — `ensure_audio_format()` already guarantees 16kHz mono before `process()` is called. Replace with a simple `soundfile.read()`.
- **Merge engine**: Set `audio_format = None` (merge doesn't consume audio files).
- **Prepare engine**: Set `audio_format = None` (prepare is the producer, not a consumer).

---

### 81.4: Optimize Prepare Stage for Already-Compliant Input

**Files modified:**

- `engines/stt-prepare/audio-prepare/engine.py`

**Deliverables:**

Add a fast path to the prepare engine itself: if the input is already 16kHz, mono, 16-bit WAV, skip the ffmpeg conversion and just copy (or reference) the file directly. This eliminates the latency penalty for well-formatted inputs.

```python
def _is_already_prepared(self, input_path: Path) -> bool:
    """Check if input already matches target format."""
    try:
        info = sf.info(str(input_path))
        return (
            info.samplerate == self.DEFAULT_SAMPLE_RATE
            and info.channels == self.DEFAULT_CHANNELS
            and info.subtype == "PCM_16"
        )
    except Exception:
        return False
```

When `_is_already_prepared` returns `True`:

- Skip ffmpeg entirely
- Still run ffprobe for metadata extraction (duration, etc.)
- Upload the original file as the prepared artifact

This makes the prepare stage a near-instant passthrough for audio that's already in the right format (common in automated pipelines where upstream systems normalize audio).

---

### 81.5: Engine Dockerfile — Ensure ffmpeg Availability

**Files modified:**

- `docker/Dockerfile.engine-base`

**Deliverables:**

Verify ffmpeg is already present in the engine base image. It almost certainly is (the prepare engine requires it, and many engines inherit from the same base). If not, add it:

```dockerfile
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
```

This is a prerequisite for the slow path (standalone engine use). In production with the prepare stage running, ffmpeg is never invoked by downstream engines — but it must be available for the guarantee to hold.

---

## Non-Goals

- **Removing the prepare stage from the DAG** — Prepare remains the default first stage. It converts once, stores a smaller file, and saves bandwidth for multiple downstream consumers. This milestone makes prepare *optional*, not *removed*.
- **Per-engine audio format negotiation at DAG build time** — The orchestrator does not inspect `engine.audio_format` to decide what prepare should output. That's a future optimization if engines ever diverge on format requirements.
- **Real-time path changes** — Real-time engines receive numpy arrays from the session handler, which already handles resampling via `torchaudio`. Out of scope.
- **Channel splitting** — Channel splitting remains exclusively in the prepare stage. It's a DAG-level concern (one input → N parallel branches) and doesn't belong in individual engines. **Important consequence:** the "prepare is optional" invariant holds only for mono/single-channel input. For `speaker_detection=per_channel`, the prepare stage is still a hard requirement — `ensure_audio_format()` downmixes to mono, which would silently destroy per-channel speaker separation. The orchestrator enforces this by always including prepare in the DAG when `split_channels=True`.

---

## Verification

```bash
make dev

# 1. Normal pipeline — prepare runs, ensure_audio_format is a no-op
export DALSTON_API_KEY=$(grep DALSTON_API_KEY .env | cut -d= -f2)
JOB_ID=$(curl -s -X POST http://localhost:8000/v1/audio/transcriptions \
  -H "Authorization: Bearer $DALSTON_API_KEY" \
  -F "file=@tests/fixtures/short.wav" | jq -r '.id')

# Poll until done — should complete normally
watch -n 2 "curl -s http://localhost:8000/v1/audio/transcriptions/$JOB_ID \
  -H 'Authorization: Bearer $DALSTON_API_KEY' | jq '{status, text}'"

# 2. Verify no conversion happened in transcribe engine logs (fast path)
docker compose logs stt-transcribe-faster-whisper-base 2>&1 | grep -i "audio.ensure" || echo "PASS: no conversion logged (fast path taken)"

# 3. Unit test: ensure_audio_format with already-compliant WAV
docker compose exec stt-transcribe-faster-whisper-base python3 -c "
from pathlib import Path
from dalston.engine_sdk.audio import ensure_audio_format, SPEECH_STANDARD
import soundfile as sf

# Create a compliant WAV
import numpy as np
sr = 16000
samples = np.zeros(sr, dtype=np.int16)
test_path = Path('/tmp/test_compliant.wav')
sf.write(str(test_path), samples, sr, subtype='PCM_16')

result = ensure_audio_format(test_path)
assert result == test_path, f'Expected same path (no-op), got {result}'
print('PASS: compliant audio returns same path')
"

# 4. Unit test: ensure_audio_format with non-compliant audio
docker compose exec stt-transcribe-faster-whisper-base python3 -c "
from pathlib import Path
from dalston.engine_sdk.audio import ensure_audio_format
import soundfile as sf
import numpy as np

# Create a 44.1kHz stereo WAV
sr = 44100
samples = np.zeros((sr, 2), dtype=np.int16)
test_path = Path('/tmp/test_stereo_44k.wav')
sf.write(str(test_path), samples, sr, subtype='PCM_16')

result = ensure_audio_format(test_path, work_dir=Path('/tmp'))
assert result != test_path, 'Expected conversion to new file'
info = sf.info(str(result))
assert info.samplerate == 16000, f'Expected 16kHz, got {info.samplerate}'
assert info.channels == 1, f'Expected mono, got {info.channels}'
print('PASS: non-compliant audio converted correctly')
"
```

---

## Checkpoint

- [ ] `dalston/engine_sdk/audio.py` implemented with `ensure_audio_format()`, `AudioFormat`, `SPEECH_STANDARD`
- [ ] Fast path: compliant WAV returns same path with no subprocess call
- [ ] Slow path: non-compliant audio converted via ffmpeg, correct output verified
- [ ] `Engine` base class gains `audio_format` class attribute (default `SPEECH_STANDARD`)
- [ ] Runner calls `ensure_audio_format()` automatically before `engine.process()`
- [ ] Alignment engine's internal resampling removed (redundant with SDK utility)
- [ ] Merge and prepare engines set `audio_format = None`
- [ ] Prepare engine skips ffmpeg for already-compliant input
- [ ] ffmpeg confirmed present in engine base Docker image
- [ ] Full pipeline (`make test-e2e`) passes with no regressions
- [ ] Unit tests cover: compliant no-op, non-compliant conversion, missing ffmpeg error, non-WAV input
