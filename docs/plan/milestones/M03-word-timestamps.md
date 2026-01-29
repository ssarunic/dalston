# M3: Word Timestamps & Alignment

| | |
|---|---|
| **Goal** | Add word-level timing to transcripts |
| **Duration** | 2-3 days |
| **Dependencies** | M2 complete |
| **Deliverable** | Transcripts include exact word timestamps |

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

- [ ] **WhisperX Align** produces word-level timestamps
- [ ] **DAG builder** conditionally includes alignment stage
- [ ] **Merger** includes words array when available
- [ ] **Pipeline** is now: prepare → transcribe → [align] → merge

**Next**: [M4: Speaker Diarization](M04-speaker-diarization.md) — Identify who said what
