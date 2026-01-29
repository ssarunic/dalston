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

```
engines/diarize/pyannote-3.1/
├── Dockerfile
├── requirements.txt
├── engine.yaml
└── engine.py
```

```python
from pyannote.audio import Pipeline

class PyannoteEngine(Engine):
    def __init__(self):
        self.pipeline = None
    
    def process(self, input: TaskInput) -> TaskOutput:
        if self.pipeline is None:
            self.pipeline = Pipeline.from_pretrained(
                "pyannote/speaker-diarization-3.1",
                use_auth_token=os.environ["HF_TOKEN"]
            )
            if torch.cuda.is_available():
                self.pipeline.to(torch.device("cuda"))
        
        audio_path = input.previous_outputs["prepare"]["audio_path"]
        
        # Optional speaker count hints
        min_speakers = input.config.get("min_speakers")
        max_speakers = input.config.get("max_speakers")
        
        diarization = self.pipeline(
            audio_path,
            min_speakers=min_speakers,
            max_speakers=max_speakers
        )
        
        segments = []
        speakers = set()
        
        for turn, _, speaker in diarization.itertracks(yield_label=True):
            segments.append({
                "start": turn.start,
                "end": turn.end,
                "speaker": speaker
            })
            speakers.add(speaker)
        
        return TaskOutput(data={
            "diarization_segments": segments,
            "speakers": sorted(speakers)
        })
```

**Note**: Requires `HF_TOKEN` environment variable for pyannote model access.

---

### 4.2: Update DAG Builder

```python
def build_task_dag(job: Job) -> list[Task]:
    params = job.parameters
    tasks = []
    
    prepare = create_task("prepare", "audio-prepare", [])
    transcribe = create_task("transcribe", "faster-whisper", [prepare.id])
    tasks.extend([prepare, transcribe])
    
    merge_deps = []
    
    # Alignment (optional, default ON)
    if params.get("word_timestamps", True):
        align = create_task("align", "whisperx-align", [transcribe.id])
        tasks.append(align)
        merge_deps.append(align.id)
    else:
        merge_deps.append(transcribe.id)
    
    # Diarization (optional)
    speaker_mode = params.get("speaker_detection", "none")
    
    if speaker_mode == "diarize":
        diarize = create_task(
            "diarize", 
            "pyannote-3.1", 
            [prepare.id],  # Can run parallel with transcribe/align!
            config={
                "min_speakers": params.get("min_speakers"),
                "max_speakers": params.get("max_speakers")
            }
        )
        tasks.append(diarize)
        merge_deps.append(diarize.id)
    
    merge = create_task("merge", "final-merger", merge_deps)
    tasks.append(merge)
    
    return tasks
```

**Note**: Diarization depends only on `prepare`, so it runs **in parallel** with transcribe/align.

---

### 4.3: Update Merger for Speaker Assignment

```python
class FinalMerger(Engine):
    def process(self, input: TaskInput) -> TaskOutput:
        prepare = input.previous_outputs.get("prepare", {})
        align_segs = input.previous_outputs.get("align", {}).get("segments", [])
        transcribe_segs = input.previous_outputs.get("transcribe", {}).get("segments", [])
        diarize_segs = input.previous_outputs.get("diarize", {}).get("diarization_segments", [])
        speakers_list = input.previous_outputs.get("diarize", {}).get("speakers", [])
        
        source_segments = align_segs or transcribe_segs
        
        segments = []
        for i, seg in enumerate(source_segments):
            # Find speaker by overlap
            speaker = self._find_speaker(seg["start"], seg["end"], diarize_segs)
            
            segments.append({
                "id": f"seg_{i:03d}",
                "start": seg["start"],
                "end": seg["end"],
                "text": seg["text"],
                "speaker": speaker,
                "words": self._format_words(seg.get("words"))
            })
        
        return TaskOutput(data={
            "text": " ".join(s["text"] for s in segments),
            "segments": segments,
            "speakers": [{"id": s, "label": None} for s in speakers_list],
            "metadata": {
                "audio_duration": prepare.get("duration"),
                "speaker_count": len(speakers_list),
                "pipeline_stages": list(input.previous_outputs.keys()) + ["merge"]
            }
        })
    
    def _find_speaker(self, start: float, end: float, diarize_segs: list) -> str | None:
        """Find speaker with maximum overlap."""
        if not diarize_segs:
            return None
        
        best_speaker = None
        best_overlap = 0
        
        for dseg in diarize_segs:
            overlap = min(end, dseg["end"]) - max(start, dseg["start"])
            if overlap > best_overlap:
                best_overlap = overlap
                best_speaker = dseg["speaker"]
        
        return best_speaker if best_overlap > 0 else None
```

---

### 4.4: Per-Channel Mode (Alternative)

For stereo recordings where each channel is a different speaker:

```python
# In DAG builder
if speaker_mode == "per_channel":
    # Analyze audio to get channel count
    prepare = create_task("prepare", "audio-prepare", [], config={"split_channels": True})
    
    # This would need audio analysis first, or be specified by user
    channel_count = params.get("num_speakers", 2)
    
    # Parallel transcription per channel
    transcribe_tasks = []
    for ch in range(channel_count):
        t = create_task(
            f"transcribe_ch{ch}",
            "faster-whisper",
            [prepare.id],
            config={"channel": ch, "audio_key": f"channel_{ch}_path"}
        )
        transcribe_tasks.append(t)
        tasks.append(t)
    
    # Special merger for channels
    merge = create_task(
        "merge",
        "channel-merger",
        [t.id for t in transcribe_tasks]
    )
```

**Channel Merger** interleaves segments by timestamp and assigns `speaker: "SPEAKER_00"` (channel 0), `"SPEAKER_01"` (channel 1), etc.

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
    {"id": "seg_001", "speaker": "SPEAKER_01", "text": "Thanks for having me."},
    {"id": "seg_002", "speaker": "SPEAKER_00", "text": "Let's dive in."}
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

## DAG Visualization

With diarization enabled, the DAG looks like:

```
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

## Checkpoint

✓ **Pyannote engine** identifies speaker turns  
✓ **DAG allows parallel** diarization and transcription  
✓ **Merger assigns speakers** to transcript segments by overlap  
✓ **Per-channel mode** available as alternative  

**Next**: [M5: Export & Webhooks](M05-export-webhooks.md) — SRT/VTT export and async notifications
