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

```
engines/align/whisperx-align/
├── Dockerfile
├── requirements.txt
├── engine.yaml
└── engine.py
```

```python
import whisperx

class WhisperXAlign(Engine):
    def __init__(self):
        self.model = None
        self.current_language = None
    
    def process(self, input: TaskInput) -> TaskOutput:
        audio_path = input.previous_outputs["prepare"]["audio_path"]
        segments = input.previous_outputs["transcribe"]["segments"]
        language = input.previous_outputs["transcribe"]["language"]
        
        # Load alignment model (language-specific)
        if self.model is None or self.current_language != language:
            self.model, self.metadata = whisperx.load_align_model(
                language_code=language,
                device="cuda"
            )
            self.current_language = language
        
        audio = whisperx.load_audio(audio_path)
        
        # Convert segments to whisperx format
        whisperx_segments = [
            {"start": s["start"], "end": s["end"], "text": s["text"]}
            for s in segments
        ]
        
        aligned = whisperx.align(
            whisperx_segments,
            self.model,
            self.metadata,
            audio,
            device="cuda"
        )
        
        return TaskOutput(data={
            "segments": aligned["segments"],
            "word_segments": aligned.get("word_segments", [])
        })
```

---

### 3.2: Update DAG Builder

```python
def build_task_dag(job: Job) -> list[Task]:
    params = job.parameters
    tasks = []
    
    prepare = create_task("prepare", "audio-prepare", [])
    transcribe = create_task("transcribe", "faster-whisper", [prepare.id])
    tasks.extend([prepare, transcribe])
    
    # Conditional alignment
    if params.get("word_timestamps", True):  # Default ON
        align = create_task("align", "whisperx-align", [transcribe.id])
        tasks.append(align)
        merge_deps = [align.id]
    else:
        merge_deps = [transcribe.id]
    
    merge = create_task("merge", "final-merger", merge_deps)
    tasks.append(merge)
    
    return tasks
```

---

### 3.3: Update Merger for Words

```python
class FinalMerger(Engine):
    def process(self, input: TaskInput) -> TaskOutput:
        prepare = input.previous_outputs.get("prepare", {})
        align_output = input.previous_outputs.get("align")
        transcribe_output = input.previous_outputs.get("transcribe", {})
        
        # Use aligned segments if available
        source_segments = (
            align_output["segments"] if align_output 
            else transcribe_output.get("segments", [])
        )
        
        segments = []
        full_text = []
        
        for i, seg in enumerate(source_segments):
            words = None
            if seg.get("words"):
                words = [
                    {
                        "word": w["word"],
                        "start": w["start"],
                        "end": w["end"],
                        "confidence": w.get("score", w.get("confidence"))
                    }
                    for w in seg["words"]
                ]
            
            segments.append({
                "id": f"seg_{i:03d}",
                "start": seg["start"],
                "end": seg["end"],
                "text": seg["text"],
                "speaker": None,
                "words": words
            })
            full_text.append(seg["text"])
        
        return TaskOutput(data={
            "text": " ".join(full_text),
            "segments": segments,
            "speakers": [],
            "metadata": {
                "audio_duration": prepare.get("duration"),
                "language": transcribe_output.get("language"),
                "word_timestamps": align_output is not None,
                "pipeline_stages": list(input.previous_outputs.keys()) + ["merge"]
            }
        })
```

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

✓ **WhisperX Align** produces word-level timestamps  
✓ **DAG builder** conditionally includes alignment stage  
✓ **Merger** includes words array when available  
✓ **Pipeline** is now: prepare → transcribe → [align] → merge  

**Next**: [M4: Speaker Diarization](M04-speaker-diarization.md) — Identify who said what
