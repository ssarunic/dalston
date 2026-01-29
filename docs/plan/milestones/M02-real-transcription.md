# M2: Real Transcription

| | |
|---|---|
| **Goal** | Replace stub transcriber with real faster-whisper |
| **Duration** | 3-4 days |
| **Dependencies** | M1 complete |
| **Deliverable** | Upload audio → get actual transcript |

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
- Output: `text`, `segments` (with start/end times), `language`, `language_probability`
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

- [ ] **Audio Prepare** converts any format to 16kHz WAV
- [ ] **Faster-Whisper** produces real transcription
- [ ] **Final Merger** outputs standard transcript format
- [ ] **Pipeline** is now: prepare → transcribe → merge

**Next**: [M3: Word Timestamps](M03-word-timestamps.md) — Add word-level alignment
