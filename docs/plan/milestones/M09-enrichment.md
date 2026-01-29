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

```
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

```
engines/detect/emotion2vec/
├── Dockerfile
├── requirements.txt
├── engine.yaml
└── engine.py
```

```python
# engine.py
from funasr import AutoModel

class Emotion2Vec(Engine):
    def __init__(self):
        self.model = None
    
    def _load_model(self):
        if self.model is None:
            self.model = AutoModel(
                model="iic/emotion2vec_plus_large",
                device="cuda"
            )
    
    def process(self, input: TaskInput) -> TaskOutput:
        self._load_model()
        
        audio_path = input.previous_outputs["prepare"]["audio_path"]
        segments = input.previous_outputs.get("align", input.previous_outputs.get("transcribe"))["segments"]
        
        results = []
        
        for seg in segments:
            # Extract segment audio
            seg_audio = extract_segment(audio_path, seg["start"], seg["end"])
            
            if len(seg_audio) < 1600:  # Skip very short segments
                results.append({
                    "segment_id": seg.get("id"),
                    "start": seg["start"],
                    "end": seg["end"],
                    "emotion": "neutral",
                    "confidence": 0.0
                })
                continue
            
            # Predict emotion
            rec = self.model.generate(seg_audio, granularity="utterance")
            
            # emotion2vec returns scores for: angry, disgusted, fearful, happy, neutral, sad, surprised
            emotion_labels = ["angry", "disgusted", "fearful", "happy", "neutral", "sad", "surprised"]
            scores = rec[0]["scores"]
            
            best_idx = scores.index(max(scores))
            emotion = emotion_labels[best_idx]
            confidence = scores[best_idx]
            
            # Simplify to positive/negative/neutral
            simplified = {
                "angry": "negative",
                "disgusted": "negative",
                "fearful": "negative",
                "sad": "negative",
                "happy": "positive",
                "surprised": "positive",
                "neutral": "neutral"
            }.get(emotion, "neutral")
            
            results.append({
                "segment_id": seg.get("id"),
                "start": seg["start"],
                "end": seg["end"],
                "emotion": simplified,
                "emotion_detailed": emotion,
                "confidence": confidence,
                "all_scores": dict(zip(emotion_labels, scores))
            })
        
        return TaskOutput(data={"emotions": results})


def extract_segment(audio_path: str, start: float, end: float) -> np.ndarray:
    """Extract audio segment using ffmpeg."""
    import subprocess
    import tempfile
    
    with tempfile.NamedTemporaryFile(suffix=".wav") as tmp:
        subprocess.run([
            "ffmpeg", "-y", "-i", audio_path,
            "-ss", str(start), "-t", str(end - start),
            "-ar", "16000", "-ac", "1",
            tmp.name
        ], capture_output=True, check=True)
        
        import soundfile as sf
        audio, _ = sf.read(tmp.name)
        return audio
```

---

### 9.2: Audio Events Engine

```
engines/detect/panns-events/
├── Dockerfile
├── requirements.txt
├── engine.yaml
└── engine.py
```

```python
# engine.py
# Using PANNs (Pretrained Audio Neural Networks) for audio tagging

class PANNsEvents(Engine):
    """Detect laughter, applause, music, coughing, etc."""
    
    RELEVANT_EVENTS = {
        "Laughter": "laughter",
        "Applause": "applause", 
        "Music": "music",
        "Cough": "cough",
        "Sigh": "sigh",
        "Crying, sobbing": "crying",
        "Cheering": "cheering",
        "Clapping": "clapping",
        "Crowd": "crowd",
        "Silence": "silence"
    }
    
    def __init__(self):
        self.model = None
    
    def process(self, input: TaskInput) -> TaskOutput:
        if self.model is None:
            from panns_inference import AudioTagging
            self.model = AudioTagging(checkpoint_path=None, device='cuda')
        
        audio_path = input.previous_outputs["prepare"]["audio_path"]
        
        # Process in windows
        import soundfile as sf
        audio, sr = sf.read(audio_path)
        
        window_size = 2.0  # 2 second windows
        hop_size = 1.0     # 1 second hop
        
        events = []
        
        for start in np.arange(0, len(audio) / sr - window_size, hop_size):
            end = start + window_size
            window = audio[int(start * sr):int(end * sr)]
            
            # Predict
            clipwise_output, _ = self.model.inference(window[None, :])
            
            # Get top predictions
            top_indices = np.argsort(clipwise_output[0])[-10:][::-1]
            
            for idx in top_indices:
                label = self.model.labels[idx]
                prob = clipwise_output[0][idx]
                
                if label in self.RELEVANT_EVENTS and prob > 0.3:
                    events.append({
                        "type": self.RELEVANT_EVENTS[label],
                        "label_original": label,
                        "start": start,
                        "end": end,
                        "confidence": float(prob)
                    })
        
        # Merge adjacent events of same type
        merged = self._merge_events(events)
        
        return TaskOutput(data={"events": merged})
    
    def _merge_events(self, events: list) -> list:
        """Merge adjacent events of the same type."""
        if not events:
            return []
        
        events = sorted(events, key=lambda e: (e["type"], e["start"]))
        merged = []
        current = None
        
        for event in events:
            if current is None:
                current = event.copy()
            elif event["type"] == current["type"] and event["start"] <= current["end"] + 0.5:
                current["end"] = max(current["end"], event["end"])
                current["confidence"] = max(current["confidence"], event["confidence"])
            else:
                merged.append(current)
                current = event.copy()
        
        if current:
            merged.append(current)
        
        return merged
```

---

### 9.3: LLM Cleanup Engine

```
engines/refine/llm-cleanup/
├── Dockerfile
├── requirements.txt
├── engine.yaml
└── engine.py
```

```python
# engine.py
import anthropic

class LLMCleanup(Engine):
    """Use Claude to fix errors, identify speakers, improve punctuation."""
    
    def __init__(self):
        self.client = anthropic.Anthropic()
    
    def process(self, input: TaskInput) -> TaskOutput:
        segments = input.previous_outputs.get("align", input.previous_outputs.get("transcribe"))["segments"]
        speakers = input.previous_outputs.get("diarize", {}).get("speakers", [])
        
        tasks = input.config.get("tasks", [
            "fix_transcription_errors",
            "identify_speakers",
            "improve_punctuation"
        ])
        
        result = {
            "segments": segments,
            "speakers": [{"id": s, "label": None} for s in speakers]
        }
        
        if "fix_transcription_errors" in tasks:
            result["segments"] = self._fix_errors(result["segments"])
        
        if "identify_speakers" in tasks and speakers:
            result["speakers"] = self._identify_speakers(result["segments"], result["speakers"])
        
        if "generate_summary" in tasks:
            result["summary"] = self._generate_summary(result["segments"])
        
        return TaskOutput(data=result)
    
    def _fix_errors(self, segments: list) -> list:
        """Send transcript to Claude for correction."""
        
        # Process in batches
        batch_size = 15
        
        for i in range(0, len(segments), batch_size):
            batch = segments[i:i + batch_size]
            batch_text = "\n".join(
                f"[{s['start']:.2f}s] {s['text']}" 
                for s in batch
            )
            
            response = self.client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=2000,
                messages=[{
                    "role": "user",
                    "content": f"""Fix obvious transcription errors in this text.
Common errors include: homophones, proper nouns, technical terms, run-on sentences.
Preserve timestamps exactly. Only fix clear errors, don't rephrase.

{batch_text}

Return ONLY the corrected text, one line per segment, same format [timestamp] text."""
                }]
            )
            
            # Parse and apply corrections
            corrected_lines = response.content[0].text.strip().split("\n")
            for j, line in enumerate(corrected_lines):
                if i + j < len(segments):
                    match = re.match(r'\[[\d.]+s\]\s*(.+)', line)
                    if match:
                        segments[i + j]["text"] = match.group(1).strip()
        
        return segments
    
    def _identify_speakers(self, segments: list, speakers: list) -> list:
        """Identify speaker names from context."""
        
        # Gather samples from each speaker
        speaker_samples = {}
        for seg in segments:
            spk = seg.get("speaker")
            if spk:
                if spk not in speaker_samples:
                    speaker_samples[spk] = []
                if len(speaker_samples[spk]) < 5:
                    speaker_samples[spk].append(seg["text"])
        
        if not speaker_samples:
            return speakers
        
        samples_text = "\n\n".join(
            f"{spk}:\n" + "\n".join(f'  "{t}"' for t in texts)
            for spk, texts in speaker_samples.items()
        )
        
        response = self.client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=500,
            messages=[{
                "role": "user",
                "content": f"""Based on this transcript, identify the speakers.
Look for: introductions, name mentions, role references, speaking style clues.

{samples_text}

Return JSON only: {{"SPEAKER_00": "Name or Role", "SPEAKER_01": "Name or Role"}}
If truly unknown, use descriptive labels like "Host", "Guest 1", "Interviewer"."""
            }]
        )
        
        try:
            # Extract JSON from response
            text = response.content[0].text
            json_match = re.search(r'\{[^}]+\}', text)
            if json_match:
                labels = json.loads(json_match.group())
                for speaker in speakers:
                    if speaker["id"] in labels:
                        speaker["label"] = labels[speaker["id"]]
        except Exception as e:
            logger.warning(f"Failed to parse speaker labels: {e}")
        
        return speakers
    
    def _generate_summary(self, segments: list) -> str:
        """Generate a summary of the transcript."""
        
        full_text = " ".join(s["text"] for s in segments)
        
        # Truncate if too long
        if len(full_text) > 10000:
            full_text = full_text[:10000] + "..."
        
        response = self.client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=500,
            messages=[{
                "role": "user",
                "content": f"""Summarize this transcript in 2-3 paragraphs.
Include: main topics, key points, notable quotes, overall tone.

{full_text}"""
            }]
        )
        
        return response.content[0].text.strip()
```

---

### 9.4: Update DAG Builder

```python
# orchestrator/dag.py

def build_task_dag(job: Job) -> list[Task]:
    params = job.parameters
    tasks = []
    
    # Core pipeline
    prepare = create_task("prepare", "audio-prepare", [])
    transcribe = create_task("transcribe", "faster-whisper", [prepare.id])
    tasks.extend([prepare, transcribe])
    
    core_deps = []
    
    # Alignment
    if params.get("word_timestamps", True):
        align = create_task("align", "whisperx-align", [transcribe.id])
        tasks.append(align)
        core_deps.append(align.id)
    else:
        core_deps.append(transcribe.id)
    
    # Diarization
    if params.get("speaker_detection") == "diarize":
        diarize = create_task("diarize", "pyannote-3.1", [prepare.id])
        tasks.append(diarize)
        core_deps.append(diarize.id)
    
    # === ENRICHMENT (parallel, optional) ===
    enrichment_tasks = []
    
    if params.get("detect_emotions"):
        emotion = create_task(
            "detect_emotions",
            "emotion2vec",
            core_deps,
            required=False  # Don't fail job if this fails
        )
        enrichment_tasks.append(emotion)
        tasks.append(emotion)
    
    if params.get("detect_events"):
        events = create_task(
            "detect_events",
            "panns-events",
            [prepare.id],  # Only needs audio
            required=False
        )
        enrichment_tasks.append(events)
        tasks.append(events)
    
    # === LLM CLEANUP (after enrichment) ===
    if params.get("llm_cleanup"):
        llm_deps = [t.id for t in enrichment_tasks] + core_deps if enrichment_tasks else core_deps
        
        llm = create_task(
            "refine",
            "llm-cleanup",
            llm_deps,
            required=False,
            config={
                "tasks": [
                    "fix_transcription_errors",
                    "identify_speakers" if params.get("speaker_detection") == "diarize" else None,
                    "generate_summary" if params.get("generate_summary") else None
                ]
            }
        )
        tasks.append(llm)
        merge_deps = [llm.id]
    else:
        merge_deps = [t.id for t in enrichment_tasks] + core_deps if enrichment_tasks else core_deps
    
    # Final merge
    merge = create_task("merge", "final-merger", merge_deps)
    tasks.append(merge)
    
    return tasks
```

---

### 9.5: Update Merger for Enrichment

```python
# engines/merge/final-merger/engine.py

class FinalMerger(Engine):
    def process(self, input: TaskInput) -> TaskOutput:
        # ... existing segment/speaker merging ...
        
        # Add emotions to segments
        emotions = input.previous_outputs.get("detect_emotions", {}).get("emotions", [])
        emotion_map = {e["segment_id"]: e for e in emotions}
        
        for seg in segments:
            if seg["id"] in emotion_map:
                seg["emotion"] = emotion_map[seg["id"]]["emotion"]
                seg["emotion_confidence"] = emotion_map[seg["id"]]["confidence"]
        
        # Add audio events
        events = input.previous_outputs.get("detect_events", {}).get("events", [])
        
        # Get LLM refinements
        refine = input.previous_outputs.get("refine", {})
        if refine:
            # Use refined segments if available
            if refine.get("segments"):
                segments = refine["segments"]
            
            # Use identified speaker labels
            if refine.get("speakers"):
                speakers = refine["speakers"]
        
        return TaskOutput(data={
            "text": " ".join(s["text"] for s in segments),
            "segments": segments,
            "speakers": speakers,
            "events": events,
            "summary": refine.get("summary"),
            "metadata": {
                # ...
                "enrichment": {
                    "emotions": len(emotions) > 0,
                    "events": len(events) > 0,
                    "llm_cleanup": bool(refine)
                }
            }
        })
```

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

# Response
{
  "segments": [
    {
      "id": "seg_000",
      "text": "Welcome to the show!",
      "speaker": "SPEAKER_00",
      "emotion": "positive",
      "emotion_confidence": 0.87
    }
  ],
  "speakers": [
    {"id": "SPEAKER_00", "label": "Host (Sarah)"},
    {"id": "SPEAKER_01", "label": "Guest (Dr. Smith)"}
  ],
  "events": [
    {"type": "laughter", "start": 45.2, "end": 47.8, "confidence": 0.92},
    {"type": "applause", "start": 120.0, "end": 125.5, "confidence": 0.88}
  ],
  "summary": "In this episode, Sarah interviews Dr. Smith about..."
}
```

---

## Checkpoint

✓ **Emotion2Vec** detects emotional tone per segment  
✓ **PANNs** detects audio events (laughter, applause, etc.)  
✓ **LLM Cleanup** fixes errors, identifies speakers, generates summary  
✓ **Enrichment is optional** and doesn't block the pipeline  
✓ **Results merged** into final transcript  

**Next**: [M10: Web Console](M10-web-console.md) — Monitoring UI
