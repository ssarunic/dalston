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

- [ ] **Pyannote engine** identifies speaker turns
- [ ] **DAG allows parallel** diarization and transcription
- [ ] **Merger assigns speakers** to transcript segments by overlap
- [ ] **Per-channel mode** available as alternative

**Next**: [M5: Export & Webhooks](M05-export-webhooks.md) — SRT/VTT export and async notifications
