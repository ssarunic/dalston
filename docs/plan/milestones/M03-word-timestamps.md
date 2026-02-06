# M3: Word Timestamps & Alignment

| | |
|---|---|
| **Goal** | Add word-level timing to transcripts |
| **Duration** | 2-3 days |
| **Dependencies** | M2 complete |
| **Deliverable** | Transcripts include exact word timestamps |
| **Status** | Completed (2026-01-30) |

## User Story

> *"As a user, I can get exact timestamps for each word, enabling subtitle generation."*

---

## Steps

### 3.1: WhisperX Alignment Engine

```text
engines/align/whisperx-align/
├── Dockerfile
├── requirements.txt
├── engine.yaml
└── engine.py
```

**Deliverables:**

- Load WhisperX alignment model (language-specific, lazy loaded)
- Align transcription segments to get word-level timestamps
- Output: aligned `segments` with `words` array containing start/end/confidence per word
- GPU Dockerfile with whisperx dependencies

---

### 3.2: Update DAG Builder

**Deliverables:**

- Make alignment conditional based on `word_timestamps` parameter (default: true)
- Pipeline: prepare → transcribe → [align] → merge
- Alignment depends on transcribe output

---

### 3.3: Update Merger for Words

**Deliverables:**

- Use aligned segments when alignment ran, otherwise use transcribe segments
- Format words array: `word`, `start`, `end`, `confidence`
- Track `word_timestamps: true/false` in metadata

---

## Verification

```bash
# Submit with word timestamps (default)
curl -X POST http://localhost:8000/v1/audio/transcriptions \
  -F "file=@speech.mp3"

# Response includes words array
{
  "segments": [
    {
      "id": "seg_000",
      "start": 0.0,
      "end": 2.5,
      "text": "Hello everyone",
      "words": [
        {"word": "Hello", "start": 0.0, "end": 0.4, "confidence": 0.98},
        {"word": "everyone", "start": 0.5, "end": 1.1, "confidence": 0.95}
      ]
    }
  ]
}

# Submit without word timestamps
curl -X POST http://localhost:8000/v1/audio/transcriptions \
  -F "file=@speech.mp3" \
  -F "word_timestamps=false"
# → segments have words: null
```

---

## Checkpoint

- [x] **WhisperX Align** produces word-level timestamps
- [x] **DAG builder** conditionally includes alignment stage
- [x] **Merger** includes words array when available
- [x] **Pipeline** is now: prepare → transcribe → [align] → merge

**Next**: [M4: Speaker Diarization](M04-speaker-diarization.md) — Identify who said what

---

## Implementation Summary

### Files Created

| File | Description |
| ---- | ----------- |
| `engines/align/whisperx-align/engine.py` | WhisperX alignment engine with lazy model loading per language |
| `engines/align/whisperx-align/engine.yaml` | Engine metadata and capability declaration |
| `engines/align/whisperx-align/requirements.txt` | Dependencies: whisperx, torch, torchaudio |
| `engines/align/whisperx-align/Dockerfile` | Container with CPU/GPU support |

### Files Modified

| File | Changes |
| ---- | ------- |
| `dalston/orchestrator/dag.py` | Added align stage, conditional based on `timestamps_granularity` or `word_timestamps` |
| `engines/merge/final-merger/engine.py` | Handle aligned segments, track `word_timestamps` and `word_timestamps_requested` in metadata |
| `docker-compose.yml` | Added `engine-whisperx-align` and `engine-whisperx-align-gpu` services |
| `CLAUDE.md` | Updated core services list to include alignment engine |

### Key Implementation Details

1. **API Compatibility**: Supports both `timestamps_granularity` (API style: "word"/"segment"/"none") and `word_timestamps` (boolean) parameters

2. **Graceful Degradation**: If alignment fails (unsupported language, model error), the engine returns original transcription segments with a warning in `pipeline_warnings`

3. **Device Detection**: Auto-detects CUDA availability, falls back to CPU

4. **Model Caching**: Language-specific wav2vec2 models are lazily loaded and cached in memory

5. **Observability**: Metadata includes:
   - `word_timestamps`: Whether words are actually available
   - `word_timestamps_requested`: Whether alignment was requested (helps distinguish "failed" vs "not requested")
   - `pipeline_warnings`: Array of any fallback events

### Docker Services

```yaml
# CPU version (default)
engine-whisperx-align

# GPU version (--profile gpu)
engine-whisperx-align-gpu
```

### Verification Results

```text
timestamps_granularity=segment → 3 tasks (prepare → transcribe → merge)
timestamps_granularity=word    → 4 tasks (prepare → transcribe → align → merge)
```
