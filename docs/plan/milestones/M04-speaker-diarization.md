# M4: Speaker Diarization

| | |
|---|---|
| **Goal** | Identify who said what |
| **Duration** | 3-4 days |
| **Dependencies** | M3 complete |
| **Deliverable** | Transcripts include speaker labels |

## User Story

> *"As a user transcribing a podcast, I can see which speaker said each segment."*

---

## Steps

### 4.1: Pyannote Diarization Engine

```text
engines/diarize/pyannote-3.1/
├── Dockerfile
├── requirements.txt
├── engine.yaml
└── engine.py
```

**Deliverables:**

- Load pyannote speaker-diarization-3.1 pipeline (requires `HF_TOKEN`)
- Support optional min/max speaker count hints
- Output: `diarization_segments` (speaker turns with start/end) and `speakers` list
- GPU Dockerfile with pyannote.audio dependencies

---

### 4.2: Update DAG Builder

**Deliverables:**

- Add `speaker_detection` parameter: `none` (default), `diarize`, `per_channel`
- Diarization depends only on `prepare` — runs **parallel** with transcribe/align
- Pass speaker count hints to diarize task config

---

### 4.3: Update Merger for Speaker Assignment

**Deliverables:**

- Assign speakers to transcript segments by finding maximum overlap with diarization segments
- Build speakers array: `{id: "SPEAKER_00", label: null}`
- Track speaker_count in metadata

---

### 4.4: Per-Channel Mode (Alternative)

For stereo recordings where each channel is a different speaker:

**Deliverables:**

- Audio prepare splits channels into separate files
- Parallel transcription per channel
- Channel merger interleaves by timestamp, assigns speaker by channel

---

## DAG Visualization

With diarization enabled:

```text
         ┌──────────┐
         │ prepare  │
         └────┬─────┘
              │
     ┌────────┴────────┐
     │                 │
     ▼                 ▼
┌──────────┐     ┌──────────┐
│transcribe│     │ diarize  │   ← Parallel!
└────┬─────┘     └────┬─────┘
     │                 │
     ▼                 │
┌──────────┐           │
│  align   │           │
└────┬─────┘           │
     │                 │
     └────────┬────────┘
              │
              ▼
         ┌──────────┐
         │  merge   │
         └──────────┘
```

---

## Verification

```bash
# Submit with diarization
curl -X POST http://localhost:8000/v1/audio/transcriptions \
  -F "file=@interview.mp3" \
  -F "speaker_detection=diarize"

# Response includes speaker labels
{
  "segments": [
    {"id": "seg_000", "speaker": "SPEAKER_00", "text": "Welcome to the show."},
    {"id": "seg_001", "speaker": "SPEAKER_01", "text": "Thanks for having me."}
  ],
  "speakers": [
    {"id": "SPEAKER_00", "label": null},
    {"id": "SPEAKER_01", "label": null}
  ]
}

# With speaker count hint
curl -X POST http://localhost:8000/v1/audio/transcriptions \
  -F "file=@podcast.mp3" \
  -F "speaker_detection=diarize" \
  -F "num_speakers=3"
```

---

## Checkpoint

- [x] **Pyannote engine** identifies speaker turns
- [x] **DAG allows parallel** diarization and transcription
- [x] **Merger assigns speakers** to transcript segments by overlap
- [x] **Per-channel mode** available as alternative

---

## Implementation Summary

### Files Created

| File | Description |
|------|-------------|
| `engines/diarize/pyannote-3.1/engine.py` | Pyannote 3.1 diarization engine with lazy loading, CUDA→CPU fallback |
| `engines/diarize/pyannote-3.1/requirements.txt` | Pinned dependencies for pyannote.audio 3.1 compatibility |
| `engines/diarize/pyannote-3.1/engine.yaml` | Engine metadata and queue configuration |
| `engines/diarize/pyannote-3.1/Dockerfile` | GPU-enabled container with CUDA support |

### Files Modified

| File | Changes |
|------|---------|
| `dalston/orchestrator/dag.py` | Added `speaker_detection` parameter (`none`/`diarize`/`per_channel`), parallel diarize task, per-channel DAG builder |
| `engines/merge/final-merger/engine.py` | Added speaker assignment via overlap algorithm, `_merge_per_channel()` for stereo audio |
| `engines/prepare/audio-prepare/engine.py` | Added `split_channels` config, `_extract_channel()` for per-channel mode, mono validation |
| `docker-compose.yml` | Added `engine-pyannote-3.1` and `engine-pyannote-3.1-gpu` services |

### Key Implementation Details

1. **Pyannote Engine**: Uses lazy model loading to avoid memory issues. Supports `DIARIZATION_DISABLED=true` env var for testing without GPU. Requires `HF_TOKEN` for HuggingFace model authentication (fails fast if missing).

2. **DAG Builder**: Diarization runs in parallel with transcribe→align chain, both depending on prepare. Merge waits for all branches to complete.

3. **Speaker Assignment**: Uses maximum overlap algorithm - for each transcript segment, finds the diarization speaker with the most temporal overlap.

4. **Per-Channel Mode**: Splits stereo audio into separate mono files, transcribes each in parallel, interleaves segments by timestamp with speaker assigned by channel.

5. **Version Pinning**: Required `numpy<2.0.0` (np.NaN removal), `huggingface_hub<0.24.0` (use_auth_token deprecation), `torch<2.5.0` (torchaudio API changes).

### Testing

Verified end-to-end with merged stereo test audio (`tests/audio/test_merged.wav`):
- Successfully identified 2 speakers from concatenated mono recordings
- Correct speaker assignment to transcript segments via overlap

**Next**: [M5: Export & Webhooks](M05-export-webhooks.md) — SRT/VTT export and async notifications
