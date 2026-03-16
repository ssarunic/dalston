# M75: NeMo Sortformer Diarizer Engine

|                  |                                                                                  |
| ---------------- | -------------------------------------------------------------------------------- |
| **Goal**         | Add NVIDIA Sortformer as a third diarization engine option alongside Pyannote and NeMo MSDD |
| **Duration**     | 1-2 days                                                                         |
| **Dependencies** | None (existing engine SDK, pipeline types, and orchestrator selection all support new engines) |
| **Deliverable**  | Batch diarization engine using `SortformerEncLabelModel` with 4-speaker streaming-capable architecture |
| **Status**       | Pending                                                                          |

## User Story

> *"As a user processing calls and meetings with up to 4 speakers, I want access to NVIDIA's Sortformer diarizer for its strong overlap handling and low DER, so I can get more accurate speaker labels without needing Pyannote's gated model access."*

---

## Overview

```text
┌──────────────────────────────────────────────────────────────────────────────┐
│                    SORTFORMER DIARIZATION ENGINE                             │
│                                                                              │
│  Existing Engines                  New Engine                                │
│  ────────────────                  ──────────                                │
│  pyannote-4.0  (unlimited spkrs)   nemo-sortformer (≤4 speakers)            │
│  nemo-msdd     (unlimited spkrs)                                            │
│                                                                              │
│  Pipeline Integration (unchanged):                                           │
│                                                                              │
│  PREPARE → TRANSCRIBE → ALIGN → DIARIZE → PII_DETECT → MERGE               │
│                                    │                                         │
│                                    ▼                                         │
│                          ┌──────────────────┐                                │
│                          │  Engine Selector  │                                │
│                          │  (dag.py)         │                                │
│                          └──────┬───────────┘                                │
│                                 │                                            │
│                    ┌────────────┼────────────┐                               │
│                    ▼            ▼            ▼                               │
│              pyannote-4.0  nemo-msdd   nemo-sortformer                      │
│                                                                              │
│  Model: nvidia/diar_streaming_sortformer_4spk-v2.1                          │
│  Framework: NeMo (SortformerEncLabelModel)                                   │
│  License: CC-BY-4.0                                                          │
│  Max speakers: 4                                                             │
│  Output: (start, end, speaker_index) tuples → SpeakerTurn                   │
│                                                                              │
│  Key advantage: End-to-end neural model with native overlap handling.        │
│  No separate embedding extraction + clustering pipeline.                     │
│  Lower DER on ≤4 speaker scenarios (5.3% CALLHOME 2-spk).                  │
└──────────────────────────────────────────────────────────────────────────────┘
```

---

## Design Decisions

### Model version: v2.1 over v2

Use `nvidia/diar_streaming_sortformer_4spk-v2.1` — identical API to v2 but dramatically better on meeting-style audio (AMI SDM DER: 31% → 21%).

### 4-speaker limit handling

The engine declares `max_speakers: 4` in its capabilities. The orchestrator's engine selector already filters by capability — jobs requesting `max_speakers > 4` or detecting more than 4 speakers in previous stages will route to pyannote or MSDD automatically.

### Batch-only in this milestone

Sortformer supports streaming inference natively, but this milestone implements batch mode only. Real-time streaming diarization is a follow-up (see Future Enhancements).

---

## Steps

### 75.1: Engine Implementation

**Deliverables:**

- Create `engines/stt-diarize/nemo-sortformer/engine.py`
- Load `SortformerEncLabelModel.from_pretrained()` on first task (lazy init)
- Accept audio file path from `EngineRequest`
- Call `model.diarize(audio=[path], batch_size=1)` to get segment tuples
- Map `(start, end, speaker_index)` tuples to `SpeakerTurn` objects
- Compute overlap statistics from segments
- Return `DiarizeOutput` with turns, speakers, overlap metrics
- Support `DALSTON_DIARIZATION_DISABLED` env var for skip mode (consistent with other engines)
- Support `DALSTON_DEVICE` env var for device selection (cuda, auto)

**Key mapping logic:**

```python
from nemo.collections.asr.models import SortformerEncLabelModel

model = SortformerEncLabelModel.from_pretrained(
    "nvidia/diar_streaming_sortformer_4spk-v2.1"
)

segments = model.diarize(audio=[audio_path], batch_size=1)[0]

turns = [
    SpeakerTurn(
        speaker=f"SPEAKER_{spk:02d}",
        start=start,
        end=end,
        confidence=None,  # Sortformer doesn't provide per-segment confidence
    )
    for start, end, spk in segments
]
```

---

### 75.2: Engine YAML

**Deliverables:**

- Create `engines/stt-diarize/nemo-sortformer/engine.yaml`
- Set `stage: diarize`, `engine_id: nemo-sortformer`
- Declare `max_speakers: 4` in capabilities
- Input: wav, 16000 Hz, mono (same as other diarizers)
- Output schema mirrors `DiarizeOutput`
- Hardware: `min_vram_gb: 4`, `supports_cpu: false`
- Config schema: `loaded_model_id` (required), `min_speakers`/`max_speakers` (optional)

---

### 75.3: Dockerfile and Requirements

**Deliverables:**

- Create `engines/stt-diarize/nemo-sortformer/Dockerfile`
  - Base: `python:3.11-slim`
  - Install system deps: `ffmpeg`, `libsndfile1`, `git`
  - Copy and install dalston package (`pip install -e ".[engine-sdk]"`)
  - Install NeMo toolkit: `pip install 'nemo_toolkit[asr]'`
  - Set model cache dirs: `HF_HOME=/models/huggingface`, `NEMO_CACHE=/models/nemo`
  - Copy `engine.yaml` and `engine.py`
  - Entrypoint: `CMD ["python", "engine.py"]`
- Create `engines/stt-diarize/nemo-sortformer/requirements.txt`
  - `nemo_toolkit[asr]`
  - Any additional Sortformer-specific deps

**Note:** Container will be similar in size to nemo-msdd (~8-10GB). Consider sharing a NeMo base image layer to reduce total disk usage if both engines are deployed.

---

### 75.4: Docker Compose Service

**Deliverables:**

- Add `stt-batch-diarize-nemo-sortformer` service to `docker-compose.yml`
- GPU profile (same pattern as nemo-msdd)
- Environment: `DALSTON_ENGINE_ID=nemo-sortformer`, `REDIS_URL`, `HF_HOME`
- Volume mount for model cache
- Health check endpoint
- Depends on: redis, minio

---

### 75.5: Engine Selector Update

**Deliverables:**

- Add `"nemo-sortformer"` as a known diarization engine in `dalston/orchestrator/dag.py` engine catalog
- Engine selector should prefer Sortformer when:
  - Job has `max_speakers <= 4` (or no speaker count hint)
  - Sortformer engine is running and available
- Fall back to pyannote/MSDD when `max_speakers > 4` or Sortformer unavailable

**Note:** Most of this routing should work automatically via capability matching. Only explicit changes needed if there's hardcoded engine preference logic.

---

### 75.6: Tests

**Deliverables:**

- Unit tests for segment-to-SpeakerTurn mapping logic
- Unit tests for overlap computation
- Unit test for skip mode (`DALSTON_DIARIZATION_DISABLED=true`)
- Unit test for max_speakers capability enforcement
- Integration test: submit job with ≤4 speakers → verify Sortformer is selected
- Integration test: submit job with >4 speakers → verify fallback to pyannote/MSDD

---

## Verification

```bash
# Build the engine
docker compose build stt-batch-diarize-nemo-sortformer

# Start with GPU profile
make dev-gpu

# Verify engine registered
curl -s http://localhost:8000/v1/engines | jq '.[] | select(.engine_id == "nemo-sortformer")'

# Submit a job (should route to Sortformer if ≤4 speakers)
JOB_ID=$(curl -s -X POST http://localhost:8000/v1/audio/transcriptions \
  -F "file=@test_2speakers.wav" \
  -F "speaker_detection=diarize" | jq -r '.id')

# Check diarization task uses Sortformer
curl -s http://localhost:8000/v1/audio/transcriptions/$JOB_ID/tasks | \
  jq '.[] | select(.stage == "diarize") | .engine_id'
# "nemo-sortformer"

# Verify output has speaker turns
curl -s http://localhost:8000/v1/audio/transcriptions/$JOB_ID | \
  jq '.utterances[:3]'

# Test fallback: submit with max_speakers > 4
JOB_ID=$(curl -s -X POST http://localhost:8000/v1/audio/transcriptions \
  -F "file=@test_6speakers.wav" \
  -F "speaker_detection=diarize" \
  -F "max_speakers=6" | jq -r '.id')

curl -s http://localhost:8000/v1/audio/transcriptions/$JOB_ID/tasks | \
  jq '.[] | select(.stage == "diarize") | .engine_id'
# "pyannote-4.0" or "nemo-msdd"
```

---

## Checkpoint

- [ ] `SortformerEncLabelModel` loads and produces diarization segments
- [ ] Output maps correctly to `DiarizeOutput` / `SpeakerTurn` types
- [ ] Overlap statistics computed from segment intersections
- [ ] `engine.yaml` declares `max_speakers: 4` capability
- [ ] Dockerfile builds and engine starts successfully
- [ ] Docker Compose service defined with GPU profile
- [ ] Engine registers in orchestrator and appears in `/v1/engines`
- [ ] Engine selector routes ≤4 speaker jobs to Sortformer
- [ ] Engine selector falls back to other engines for >4 speakers
- [ ] Skip mode works with `DALSTON_DIARIZATION_DISABLED=true`
- [ ] Unit and integration tests passing

---

## Future Enhancements

1. **Real-time streaming diarization**: Sortformer natively supports streaming with configurable latency (0.32s–30.4s). Integrate as a real-time engine via the realtime SDK + WebSocket for live speaker labels during streaming transcription. This would be a unique capability — live "who is speaking" alongside streaming ASR.
2. **Latency presets**: Expose streaming latency configuration (ultra-low/low/high/very-high) as engine config parameters for tuning the accuracy-latency tradeoff.
3. **Confidence scores**: Use the raw probability tensor output (`include_tensor_outputs=True`) to derive per-segment confidence scores from the T×4 sigmoid probability matrix.
4. **NVIDIA Riva deployment**: For production at scale, consider deploying via Riva gRPC service instead of direct NeMo, avoiding the heavy NeMo framework dependency.
5. **Shared NeMo base image**: If both nemo-msdd and nemo-sortformer are deployed, create a shared NeMo base Docker image to reduce total disk usage.
