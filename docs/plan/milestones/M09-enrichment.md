# M9: Enrichment & Refinement

| | |
|---|---|
| **Goal** | Add optional enrichment features |
| **Duration** | 4-5 days |
| **Dependencies** | M4 complete (diarization) |
| **Deliverable** | Emotion detection, audio events, LLM cleanup |

## User Story

> *"As a user, I can get emotional tone analysis for each segment."*

> *"As a user, I can have an LLM fix transcription errors and identify speakers by name."*

---

## Overview

Enrichment stages are:

- **Optional**: Don't block the pipeline if they fail
- **Parallel**: Can run alongside each other
- **Post-core**: Run after transcription, alignment, diarization

```text
prepare → transcribe → align → diarize ─┬─→ emotions (optional)
                                         ├─→ events (optional)
                                         └─→ llm-cleanup (optional)
                                               │
                                               ▼
                                             merge
```

---

## Steps

### 9.1: Emotion Detection Engine

```text
engines/detect/emotion2vec/
├── Dockerfile
├── requirements.txt
├── engine.yaml
└── engine.py
```

**Deliverables:**

- Use emotion2vec (FunASR) model for utterance-level emotion
- Process each segment from align/transcribe output
- Output: emotion label, confidence, detailed scores per segment
- Map to simplified categories: positive/negative/neutral

**Emotion Labels:** angry, disgusted, fearful, happy, neutral, sad, surprised

---

### 9.2: Audio Events Engine

```text
engines/detect/panns-events/
├── Dockerfile
├── requirements.txt
├── engine.yaml
└── engine.py
```

**Deliverables:**

- Use PANNs (Pretrained Audio Neural Networks) for audio tagging
- Process in 2-second windows with 1-second hop
- Detect relevant events: laughter, applause, music, cough, sigh, crying, cheering, clapping, crowd, silence
- Merge adjacent events of same type
- Confidence threshold: 0.3

---

### 9.3: LLM Cleanup Engine

```text
engines/refine/llm-cleanup/
├── Dockerfile
├── requirements.txt
├── engine.yaml
└── engine.py
```

**Deliverables:**

- Use Claude API for text refinement
- Tasks configurable via `config.tasks`:
  - `fix_transcription_errors`: Fix homophones, proper nouns, technical terms
  - `identify_speakers`: Infer speaker names from context clues
  - `generate_summary`: 2-3 paragraph summary of transcript
- Process in batches (15 segments per batch)
- Preserve timestamps exactly

---

### 9.4: Update DAG Builder

**Deliverables:**

- Add enrichment task creation based on parameters:
  - `detect_emotions` → emotion2vec task
  - `detect_events` → panns-events task
  - `llm_cleanup` → llm-cleanup task
- Enrichment tasks have `required=False` (don't fail job)
- Tasks run in parallel after core pipeline
- LLM cleanup waits for enrichment tasks if present

---

### 9.5: Update Merger for Enrichment

**Deliverables:**

- Merge emotions into segments (`emotion`, `emotion_confidence`)
- Include audio events array in output
- Apply LLM refinements (corrected text, speaker labels, summary)
- Track which enrichments were applied in `metadata.enrichment`

---

## Verification

```bash
# Submit with enrichment options
curl -X POST http://localhost:8000/v1/audio/transcriptions \
  -F "file=@podcast.mp3" \
  -F "speaker_detection=diarize" \
  -F "detect_emotions=true" \
  -F "detect_events=true" \
  -F "llm_cleanup=true" \
  -F "generate_summary=true"
```

**Expected response includes:**

- `segments[].emotion` and `segments[].emotion_confidence`
- `speakers[].label` with inferred names
- `events[]` array with detected audio events
- `summary` field with transcript summary

---

## Checkpoint

- [ ] **Emotion2Vec** detects emotional tone per segment
- [ ] **PANNs** detects audio events (laughter, applause, etc.)
- [ ] **LLM Cleanup** fixes errors, identifies speakers, generates summary
- [ ] **Enrichment is optional** and doesn't block the pipeline
- [ ] **Results merged** into final transcript

**Next**: [M10: Web Console](M10-web-console.md) — Monitoring UI
