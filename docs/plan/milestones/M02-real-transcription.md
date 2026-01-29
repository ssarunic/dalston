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

```
engines/prepare/audio-prepare/
├── Dockerfile
├── requirements.txt
├── engine.yaml
└── engine.py
```

Converts any audio format to 16kHz, 16-bit, mono WAV:

```python
class AudioPrepare(Engine):
    def process(self, input: TaskInput) -> TaskOutput:
        output_path = Path(input.audio_path).parent / "prepared.wav"
        
        subprocess.run([
            "ffmpeg", "-y", "-i", str(input.audio_path),
            "-ar", "16000", "-ac", "1", "-sample_fmt", "s16",
            str(output_path)
        ], check=True)
        
        # Get duration
        probe = subprocess.run([
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(output_path)
        ], capture_output=True, text=True)
        
        return TaskOutput(data={
            "audio_path": str(output_path),
            "duration": float(probe.stdout.strip()),
            "sample_rate": 16000,
            "channels": 1
        })
```

**Dockerfile**:
```dockerfile
FROM python:3.11-slim

RUN apt-get update && apt-get install -y ffmpeg && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
CMD ["python", "engine.py"]
```

---

### 2.2: Faster-Whisper Engine

```
engines/transcribe/faster-whisper/
├── Dockerfile
├── requirements.txt
├── engine.yaml
└── engine.py
```

```python
from faster_whisper import WhisperModel

class FasterWhisperEngine(Engine):
    def __init__(self):
        self.model = None
    
    def _load_model(self, model_size: str = "large-v3"):
        if self.model is None:
            self.model = WhisperModel(
                model_size, 
                device="cuda", 
                compute_type="float16"
            )
    
    def process(self, input: TaskInput) -> TaskOutput:
        self._load_model(input.config.get("model", "large-v3"))
        
        audio_path = input.previous_outputs["prepare"]["audio_path"]
        
        segments, info = self.model.transcribe(
            audio_path,
            language=input.config.get("language"),
            beam_size=5,
            vad_filter=True
        )
        
        result_segments = []
        full_text = []
        
        for seg in segments:
            result_segments.append({
                "start": seg.start,
                "end": seg.end,
                "text": seg.text.strip()
            })
            full_text.append(seg.text.strip())
        
        return TaskOutput(data={
            "text": " ".join(full_text),
            "segments": result_segments,
            "language": info.language,
            "language_probability": info.language_probability
        })
```

**Dockerfile** (GPU):
```dockerfile
FROM nvidia/cuda:12.1-runtime-ubuntu22.04

RUN apt-get update && apt-get install -y python3 python3-pip && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt

# Pre-download model
RUN python3 -c "from faster_whisper import WhisperModel; WhisperModel('large-v3', device='cpu')"

COPY . .
CMD ["python3", "engine.py"]
```

---

### 2.3: Update DAG Builder

```python
# orchestrator/dag.py

def build_task_dag(job: Job) -> list[Task]:
    tasks = []
    
    # 1. Prepare (always first)
    prepare = create_task(
        job_id=job.id,
        stage="prepare",
        engine_id="audio-prepare",
        dependencies=[]
    )
    tasks.append(prepare)
    
    # 2. Transcribe (depends on prepare)
    transcribe = create_task(
        job_id=job.id,
        stage="transcribe",
        engine_id="faster-whisper",
        dependencies=[prepare.id],
        config={"model": job.parameters.get("model", "large-v3")}
    )
    tasks.append(transcribe)
    
    # 3. Merge (depends on transcribe)
    merge = create_task(
        job_id=job.id,
        stage="merge",
        engine_id="final-merger",
        dependencies=[transcribe.id]
    )
    tasks.append(merge)
    
    return tasks
```

---

### 2.4: Final Merger Engine

```python
class FinalMerger(Engine):
    def process(self, input: TaskInput) -> TaskOutput:
        prepare = input.previous_outputs.get("prepare", {})
        transcribe = input.previous_outputs.get("transcribe", {})
        
        return TaskOutput(data={
            "text": transcribe.get("text", ""),
            "segments": [
                {
                    "id": f"seg_{i:03d}",
                    "start": seg["start"],
                    "end": seg["end"],
                    "text": seg["text"],
                    "speaker": None,
                    "words": None
                }
                for i, seg in enumerate(transcribe.get("segments", []))
            ],
            "speakers": [],
            "metadata": {
                "audio_duration": prepare.get("duration"),
                "language": transcribe.get("language"),
                "pipeline_stages": ["prepare", "transcribe", "merge"]
            }
        })
```

---

### 2.5: Update Docker Compose

```yaml
services:
  # ... postgres, redis, gateway, orchestrator unchanged ...

  engine-audio-prepare:
    build: { context: ./engines/prepare/audio-prepare }
    environment:
      - REDIS_URL=redis://redis:6379
      - ENGINE_ID=audio-prepare
      - S3_BUCKET=${S3_BUCKET}
      - S3_REGION=${S3_REGION}
      - AWS_ACCESS_KEY_ID=${AWS_ACCESS_KEY_ID}
      - AWS_SECRET_ACCESS_KEY=${AWS_SECRET_ACCESS_KEY}
    tmpfs: ["/tmp/dalston:size=10G"]
    depends_on: [redis]

  engine-faster-whisper:
    build: { context: ./engines/transcribe/faster-whisper }
    environment:
      - REDIS_URL=redis://redis:6379
      - ENGINE_ID=faster-whisper
      - S3_BUCKET=${S3_BUCKET}
      - S3_REGION=${S3_REGION}
      - AWS_ACCESS_KEY_ID=${AWS_ACCESS_KEY_ID}
      - AWS_SECRET_ACCESS_KEY=${AWS_SECRET_ACCESS_KEY}
    tmpfs: ["/tmp/dalston:size=10G"]
    volumes: [model-cache:/models]
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: 1
              capabilities: [gpu]
    depends_on: [redis]

  engine-final-merger:
    build: { context: ./engines/merge/final-merger }
    environment:
      - REDIS_URL=redis://redis:6379
      - ENGINE_ID=final-merger
      - S3_BUCKET=${S3_BUCKET}
      - S3_REGION=${S3_REGION}
      - AWS_ACCESS_KEY_ID=${AWS_ACCESS_KEY_ID}
      - AWS_SECRET_ACCESS_KEY=${AWS_SECRET_ACCESS_KEY}
    tmpfs: ["/tmp/dalston:size=1G"]
    depends_on: [redis]

volumes:
  model-cache:
```

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
# → {
#     "id": "job_xyz789",
#     "status": "completed",
#     "text": "Welcome to the podcast...",
#     "segments": [...],
#     "metadata": {"language": "en", "audio_duration": 45.2}
#   }
```

---

## Checkpoint

✓ **Audio Prepare** converts any format to 16kHz WAV  
✓ **Faster-Whisper** produces real transcription  
✓ **Final Merger** outputs standard transcript format  
✓ **Pipeline** is now: prepare → transcribe → merge  

**Next**: [M3: Word Timestamps](M03-word-timestamps.md) — Add word-level alignment
