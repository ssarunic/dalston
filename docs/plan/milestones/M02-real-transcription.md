# M2: Real Transcription

| | |
|---|---|
| **Goal** | Replace stub transcriber with real faster-whisper |
| **Duration** | 3-4 days |
| **Dependencies** | M1 complete |
| **Deliverable** | Upload audio → get actual transcript |
| **Status** | Completed (2026-01-30) |

## User Story

> *"As a user, I can upload audio and get an actual transcript."*

---

## Steps

### 2.1: Audio Prepare Engine

```text
engines/prepare/audio-prepare/
├── Dockerfile
├── requirements.txt
├── engine.yaml
└── engine.py
```

**Deliverables:**

- Convert any audio format to 16kHz, 16-bit, mono WAV using ffmpeg
- Extract duration using ffprobe
- Output: `audio_path`, `duration`, `sample_rate`, `channels`
- Dockerfile with ffmpeg installed

---

### 2.2: Faster-Whisper Engine

```text
engines/transcribe/faster-whisper/
├── Dockerfile
├── requirements.txt
├── engine.yaml
└── engine.py
```

**Deliverables:**

- Load faster-whisper model (lazy loading on first request)
- Support model size config (default: `large-v3`)
- Enable VAD filtering for better accuracy
- Output: `text`, `segments` (with start/end times), `language`, `language_confidence`
- GPU Dockerfile with CUDA 12.1, model pre-downloaded at build time

---

### 2.3: Update DAG Builder

**Deliverables:**

- Update `orchestrator/dag.py` to produce 3-task pipeline:
  - `prepare` (audio-prepare) → `transcribe` (faster-whisper) → `merge` (final-merger)
- Pass model config from job parameters to transcribe task

---

### 2.4: Final Merger Engine

```text
engines/merge/final-merger/
├── Dockerfile
├── requirements.txt
├── engine.yaml
└── engine.py
```

**Deliverables:**

- Combine outputs from prepare and transcribe stages
- Generate segment IDs (`seg_000`, `seg_001`, etc.)
- Add metadata: audio_duration, language, pipeline_stages
- Output standard transcript format (empty speakers/words for now)

---

### 2.5: Update Docker Compose

**New services:**

| Service | Image | GPU | Purpose |
| --- | --- | --- | --- |
| `engine-audio-prepare` | audio-prepare | No | FFmpeg conversion |
| `engine-faster-whisper` | faster-whisper | Yes | Transcription |
| `engine-final-merger` | final-merger | No | Output assembly |

**Configuration:**

- All engines need: `REDIS_URL`, `ENGINE_ID`, S3 credentials
- faster-whisper needs: GPU reservation, model cache volume
- tmpfs mounts for processing workspace

---

## Verification

```bash
# Rebuild with real engines
docker compose up -d --build

# Submit real audio
curl -X POST http://localhost:8000/v1/audio/transcriptions \
  -F "file=@podcast_clip.mp3"
# → {"id": "job_xyz789", "status": "pending"}

# Poll until complete
curl http://localhost:8000/v1/audio/transcriptions/job_xyz789
# → {"status": "completed", "text": "Welcome to the podcast...", ...}
```

---

## Checkpoint

- [x] **Audio Prepare** converts any format to 16kHz WAV
- [x] **Faster-Whisper** produces real transcription
- [x] **Final Merger** outputs standard transcript format
- [x] **Pipeline** is now: prepare → transcribe → merge

**Next**: [M3: Word Timestamps](M03-word-timestamps.md) — Add word-level alignment

---

## Implementation Notes (2026-01-30)

### What Was Implemented

1. **Audio Prepare Engine** (`engines/prepare/audio-prepare/`)
   - FFmpeg-based conversion to 16kHz mono WAV
   - Duration extraction using ffprobe
   - Lightweight Python 3.11-slim base image

2. **Faster-Whisper Engine** (`engines/transcribe/faster-whisper/`)
   - CTranslate2-based transcription with `large-v3` model
   - Auto-detection of GPU vs CPU mode (falls back to int8 quantization on CPU)
   - VAD filtering enabled by default
   - Word-level timestamps enabled
   - Model pre-downloaded at Docker build time for faster startup

3. **Final Merger Engine** (`engines/merge/final-merger/`)
   - Combines transcription output into canonical `transcript.json`
   - Writes to `s3://{bucket}/jobs/{job_id}/transcript.json`

4. **DAG Builder Updates** (`dalston/orchestrator/dag.py`)
   - Hardcoded engine IDs to avoid environment variable confusion
   - 3-stage pipeline: `prepare` → `transcribe` → `merge`

5. **Stub Engines Removed**
   - `stub-transcriber` and `stub-merger` were M01 scaffolding
   - Removed from docker-compose.yml and codebase

### Key Fixes

- **Language "auto" handling**: faster-whisper expects `None` for auto-detection, not `"auto"` string. Fixed in `engine.py:97-100`.
- **CPU mode support**: Rewrote Dockerfile to use `python:3.11-slim` instead of NVIDIA CUDA base, enabling CPU-only deployments.
- **Duplicate task bug**: Traced to local orchestrator process running alongside Docker orchestrator, both subscribing to same Redis pub/sub channel.

### Future Work / Notes

- **GPU support**: The `engine-faster-whisper-gpu` service in docker-compose.yml provides GPU acceleration via the `--profile gpu` flag. Not tested in this milestone.
- **Model caching**: The `whisper-models` volume persists downloaded models between container restarts.
- **Scaling**: Can scale faster-whisper with `docker compose up -d --scale engine-faster-whisper=N`.
- **Word timestamps**: Already enabled in faster-whisper output, but Gateway response doesn't expose them yet (M03 scope).
