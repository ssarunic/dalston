# Real-Time Engines

## Overview

Real-time engines are WebSocket servers that handle streaming audio transcription. Unlike batch engines that poll Redis queues, real-time engines maintain direct connections with clients.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                         REALTIME ENGINE WORKER                                   │
│                                                                                  │
│   ┌─────────────────────────────────────────────────────────────────────────┐   │
│   │                        WebSocket Server                                  │   │
│   │                        (e.g., port 9000)                                │   │
│   │                                                                          │   │
│   │   /session                                                               │   │
│   │     • Accept new transcription session                                  │   │
│   │     • Bidirectional: audio in, transcripts out                         │   │
│   │                                                                          │   │
│   │   /health                                                                │   │
│   │     • Health check endpoint                                             │   │
│   │                                                                          │   │
│   └─────────────────────────────────────────────────────────────────────────┘   │
│                                                                                  │
│   ┌─────────────────────────────────────────────────────────────────────────┐   │
│   │                        Model Manager                                     │   │
│   │                                                                          │   │
│   │   • Load ASR models on startup                                          │   │
│   │   • Keep models in GPU memory                                           │   │
│   │   • Support multiple model variants                                     │   │
│   │                                                                          │   │
│   │   Loaded models:                                                        │   │
│   │     - distil-whisper (fast, low latency)                               │   │
│   │     - faster-whisper large-v3 (accurate)                               │   │
│   │                                                                          │   │
│   └─────────────────────────────────────────────────────────────────────────┘   │
│                                                                                  │
│   ┌─────────────────────────────────────────────────────────────────────────┐   │
│   │                   Registry Heartbeat                                     │   │
│   │                                                                          │   │
│   │   Every 10s:                                                            │   │
│   │     → Update dalston:realtime:worker:{id}                               │   │
│   │     → Report: active_sessions, gpu_memory, status                       │   │
│   │                                                                          │   │
│   └─────────────────────────────────────────────────────────────────────────┘   │
│                                                                                  │
│   ┌─────────────────────────────────────────────────────────────────────────┐   │
│   │                   Session Handlers (per connection)                      │   │
│   │                                                                          │   │
│   │   Session 1: [Audio Buffer] → [VAD] → [ASR] → [Assembler] → [Output]   │   │
│   │   Session 2: [Audio Buffer] → [VAD] → [ASR] → [Assembler] → [Output]   │   │
│   │   Session 3: [Audio Buffer] → [VAD] → [ASR] → [Assembler] → [Output]   │   │
│   │   Session 4: [Audio Buffer] → [VAD] → [ASR] → [Assembler] → [Output]   │   │
│   │                                                                          │   │
│   └─────────────────────────────────────────────────────────────────────────┘   │
│                                                                                  │
└─────────────────────────────────────────────────────────────────────────────────┘
```

---

## Session Handler Pipeline

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                         SESSION HANDLER PIPELINE                                 │
│                                                                                  │
│                                                                                  │
│   ┌───────────────┐                                                             │
│   │ Audio Receiver│  ← Binary WebSocket frames                                  │
│   │               │                                                             │
│   │ • Validate    │  Check format matches config                               │
│   │ • Buffer      │  Accumulate until chunk threshold                          │
│   │               │                                                             │
│   └───────┬───────┘                                                             │
│           │                                                                      │
│           │ Audio chunk (100-250ms)                                             │
│           ▼                                                                      │
│   ┌───────────────┐                                                             │
│   │  VAD Engine   │  Silero VAD                                                │
│   │               │                                                             │
│   │ • Speech      │  Probability that audio contains speech                    │
│   │   probability │                                                             │
│   │               │                                                             │
│   │ • State       │  SILENCE → SPEECH → SILENCE                                │
│   │   machine     │                                                             │
│   │               │                                                             │
│   │ • Endpoint    │  Detect end of utterance                                   │
│   │   detection   │  (configurable silence duration)                           │
│   │               │                                                             │
│   └───────┬───────┘                                                             │
│           │                                                                      │
│           │ On speech: accumulate audio                                         │
│           │ On endpoint: trigger ASR                                            │
│           ▼                                                                      │
│   ┌───────────────┐                                                             │
│   │  ASR Engine   │  Whisper / faster-whisper                                  │
│   │               │                                                             │
│   │ • Transcribe  │  Process accumulated speech audio                          │
│   │               │                                                             │
│   │ • Output      │  Text + word timestamps + confidence                       │
│   │               │                                                             │
│   └───────┬───────┘                                                             │
│           │                                                                      │
│           │ Transcription result                                                │
│           ▼                                                                      │
│   ┌───────────────┐                                                             │
│   │  Transcript   │                                                             │
│   │  Assembler    │                                                             │
│   │               │                                                             │
│   │ • Accumulate  │  Build full session transcript                             │
│   │ • Timestamp   │  Adjust timestamps to session time                         │
│   │ • Dedupe      │  Handle overlapping transcriptions                         │
│   │               │                                                             │
│   └───────┬───────┘                                                             │
│           │                                                                      │
│           │ Final or partial result                                             │
│           ▼                                                                      │
│   ┌───────────────┐                                                             │
│   │ Result Sender │  → JSON WebSocket frames                                   │
│   │               │                                                             │
│   │ • Serialize   │  Create protocol messages                                  │
│   │ • Send        │  Push to client WebSocket                                  │
│   │               │                                                             │
│   └───────────────┘                                                             │
│                                                                                  │
└─────────────────────────────────────────────────────────────────────────────────┘
```

---

## Components

### Model Manager

Loads and manages ASR models in GPU memory.

```python
class ModelManager:
    def __init__(self, models_config: dict):
        self.models = {}
        
    async def load_models(self):
        """Load configured models into GPU memory."""
        # Load fast model (distil-whisper)
        self.models["fast"] = WhisperModel(
            "distil-whisper/distil-large-v3",
            device="cuda",
            compute_type="float16"
        )
        
        # Load accurate model (faster-whisper large-v3)
        self.models["accurate"] = WhisperModel(
            "large-v3",
            device="cuda",
            compute_type="float16"
        )
    
    def get_model(self, variant: str) -> WhisperModel:
        """Get model by variant name."""
        return self.models.get(variant, self.models["fast"])
    
    def get_gpu_memory_usage(self) -> str:
        """Report GPU memory usage."""
        return f"{torch.cuda.memory_allocated() / 1e9:.1f}GB"
```

### VAD Engine

Voice Activity Detection using Silero VAD.

```python
class VADEngine:
    def __init__(self, config: VADConfig):
        self.model = self._load_silero_vad()
        self.config = config
        self.state = VADState.SILENCE
        self.speech_buffer = []
        self.silence_duration = 0
        
    def process_chunk(self, audio: np.ndarray) -> VADResult:
        """Process audio chunk and detect speech."""
        
        # Get speech probability
        prob = self.model(torch.from_numpy(audio))
        
        is_speech = prob > self.config.speech_threshold
        
        if self.state == VADState.SILENCE:
            if is_speech:
                self.state = VADState.SPEECH
                self.speech_buffer = [audio]
                return VADResult(
                    event=VADEvent.SPEECH_START,
                    audio=None
                )
        
        elif self.state == VADState.SPEECH:
            if is_speech:
                self.speech_buffer.append(audio)
                self.silence_duration = 0
            else:
                self.silence_duration += len(audio) / self.config.sample_rate
                
                if self.silence_duration >= self.config.endpoint_silence:
                    # Endpoint detected
                    self.state = VADState.SILENCE
                    speech_audio = np.concatenate(self.speech_buffer)
                    self.speech_buffer = []
                    self.silence_duration = 0
                    
                    return VADResult(
                        event=VADEvent.SPEECH_END,
                        audio=speech_audio
                    )
                else:
                    # Brief pause, keep buffering
                    self.speech_buffer.append(audio)
        
        return VADResult(event=None, audio=None)

@dataclass
class VADConfig:
    speech_threshold: float = 0.5
    endpoint_silence: float = 0.5  # Seconds of silence to trigger endpoint
    sample_rate: int = 16000

class VADState(Enum):
    SILENCE = "silence"
    SPEECH = "speech"

class VADEvent(Enum):
    SPEECH_START = "speech_start"
    SPEECH_END = "speech_end"

@dataclass
class VADResult:
    event: VADEvent | None
    audio: np.ndarray | None  # Speech audio if endpoint detected
```

### ASR Engine

Streaming-optimized Whisper transcription.

```python
class ASREngine:
    def __init__(self, model_manager: ModelManager):
        self.model_manager = model_manager
        
    def transcribe(
        self,
        audio: np.ndarray,
        model_variant: str,
        language: str
    ) -> ASRResult:
        """Transcribe speech audio."""
        
        model = self.model_manager.get_model(model_variant)
        
        segments, info = model.transcribe(
            audio,
            language=None if language == "auto" else language,
            word_timestamps=True,
            vad_filter=False  # We already did VAD
        )
        
        words = []
        text_parts = []
        
        for segment in segments:
            text_parts.append(segment.text.strip())
            
            if segment.words:
                for word in segment.words:
                    words.append(Word(
                        word=word.word,
                        start=word.start,
                        end=word.end,
                        confidence=word.probability
                    ))
        
        return ASRResult(
            text=" ".join(text_parts),
            words=words,
            language=info.language,
            confidence=sum(w.confidence for w in words) / len(words) if words else 0
        )

@dataclass
class Word:
    word: str
    start: float
    end: float
    confidence: float

@dataclass
class ASRResult:
    text: str
    words: list[Word]
    language: str
    confidence: float
```

### Transcript Assembler

Builds the full session transcript from utterances.

```python
class TranscriptAssembler:
    def __init__(self):
        self.segments = []
        self.current_time = 0.0
        
    def add_utterance(self, result: ASRResult, audio_duration: float) -> Segment:
        """Add transcribed utterance to session transcript."""
        
        # Adjust timestamps to session time
        segment = Segment(
            id=f"seg_{len(self.segments):04d}",
            start=self.current_time,
            end=self.current_time + audio_duration,
            text=result.text,
            words=[
                Word(
                    word=w.word,
                    start=self.current_time + w.start,
                    end=self.current_time + w.end,
                    confidence=w.confidence
                )
                for w in result.words
            ],
            confidence=result.confidence
        )
        
        self.segments.append(segment)
        self.current_time = segment.end
        
        return segment
    
    def get_full_transcript(self) -> str:
        """Get full session transcript."""
        return " ".join(s.text for s in self.segments)
    
    def get_all_segments(self) -> list[Segment]:
        """Get all segments."""
        return self.segments

@dataclass
class Segment:
    id: str
    start: float
    end: float
    text: str
    words: list[Word]
    confidence: float
```

---

## Implementation

### Engine Entry Point

```python
# engines/realtime/whisper-streaming/engine.py

import asyncio
import json
import os
from websockets import serve, WebSocketServerProtocol

from dalston_realtime_sdk import (
    RealtimeEngine,
    ModelManager,
    SessionHandler,
    RegistryClient
)


class WhisperStreamingEngine(RealtimeEngine):
    def __init__(self):
        self.worker_id = os.environ["WORKER_ID"]
        self.port = int(os.environ.get("WORKER_PORT", 9000))
        self.max_sessions = int(os.environ.get("MAX_SESSIONS", 4))
        
        self.model_manager = ModelManager()
        self.registry = RegistryClient(os.environ["REDIS_URL"])
        self.sessions: dict[str, SessionHandler] = {}
        
    async def start(self):
        """Start the engine."""
        
        # Load models
        await self.model_manager.load_models()
        
        # Register with Session Router
        await self.registry.register(
            worker_id=self.worker_id,
            endpoint=f"ws://localhost:{self.port}",
            capacity=self.max_sessions,
            models=list(self.model_manager.models.keys()),
            languages=["en", "es", "fr", "de", "auto"]
        )
        
        # Start heartbeat
        asyncio.create_task(self.heartbeat_loop())
        
        # Start WebSocket server
        async with serve(self.handle_connection, "0.0.0.0", self.port):
            print(f"Realtime engine listening on port {self.port}")
            await asyncio.Future()  # Run forever
    
    async def heartbeat_loop(self):
        """Send periodic heartbeats to Session Router."""
        while True:
            await self.registry.heartbeat(
                worker_id=self.worker_id,
                active_sessions=len(self.sessions),
                gpu_memory=self.model_manager.get_gpu_memory_usage()
            )
            await asyncio.sleep(10)
    
    async def handle_connection(self, websocket: WebSocketServerProtocol, path: str):
        """Handle new WebSocket connection."""
        
        if path == "/health":
            await websocket.send(json.dumps({"status": "healthy"}))
            return
        
        if path != "/session":
            await websocket.close(1008, "Invalid path")
            return
        
        if len(self.sessions) >= self.max_sessions:
            await websocket.close(1013, "Server at capacity")
            return
        
        # Parse query parameters
        params = self._parse_query_params(websocket.request_headers.get("uri", ""))
        
        # Create session handler
        session = SessionHandler(
            websocket=websocket,
            model_manager=self.model_manager,
            config=SessionConfig(
                language=params.get("language", "auto"),
                model=params.get("model", "fast"),
                interim_results=params.get("interim_results", "true") == "true",
                word_timestamps=params.get("word_timestamps", "false") == "true",
                enable_vad=params.get("enable_vad", "true") == "true"
            )
        )
        
        self.sessions[session.session_id] = session
        
        try:
            await session.run()
        finally:
            del self.sessions[session.session_id]
            await self.registry.session_ended(
                worker_id=self.worker_id,
                session_id=session.session_id,
                duration=session.get_duration(),
                status="completed" if not session.error else "error"
            )


if __name__ == "__main__":
    engine = WhisperStreamingEngine()
    asyncio.run(engine.start())
```

### Session Handler

```python
class SessionHandler:
    def __init__(
        self,
        websocket: WebSocketServerProtocol,
        model_manager: ModelManager,
        config: SessionConfig
    ):
        self.websocket = websocket
        self.model_manager = model_manager
        self.config = config
        
        self.session_id = f"sess_{generate_id()}"
        self.started_at = time.time()
        self.error = None
        
        self.vad = VADEngine(VADConfig())
        self.asr = ASREngine(model_manager)
        self.assembler = TranscriptAssembler()
        
        self.audio_buffer = AudioBuffer(
            sample_rate=config.sample_rate,
            channels=config.channels,
            encoding=config.encoding
        )
        
    async def run(self):
        """Main session loop."""
        
        # Send session begin
        await self.send({
            "type": "session.begin",
            "session_id": self.session_id,
            "config": {
                "sample_rate": self.config.sample_rate,
                "encoding": self.config.encoding,
                "channels": self.config.channels,
                "language": self.config.language,
                "model": self.config.model
            }
        })
        
        try:
            async for message in self.websocket:
                if isinstance(message, bytes):
                    await self.handle_audio(message)
                else:
                    await self.handle_control(json.loads(message))
                    
        except Exception as e:
            self.error = str(e)
            await self.send({
                "type": "error",
                "code": "internal_error",
                "message": str(e),
                "recoverable": False
            })
    
    async def handle_audio(self, data: bytes):
        """Process incoming audio data."""
        
        # Add to buffer
        self.audio_buffer.add(data)
        
        # Process in chunks
        while chunk := self.audio_buffer.get_chunk():
            await self.process_chunk(chunk)
    
    async def process_chunk(self, audio: np.ndarray):
        """Process a single audio chunk through the pipeline."""
        
        # VAD
        vad_result = self.vad.process_chunk(audio)
        
        if vad_result.event == VADEvent.SPEECH_START:
            if self.config.enable_vad:
                await self.send({
                    "type": "vad.speech_start",
                    "timestamp": self.assembler.current_time
                })
        
        elif vad_result.event == VADEvent.SPEECH_END:
            if self.config.enable_vad:
                await self.send({
                    "type": "vad.speech_end",
                    "timestamp": self.assembler.current_time
                })
            
            # Transcribe the speech
            if vad_result.audio is not None and len(vad_result.audio) > 0:
                asr_result = self.asr.transcribe(
                    vad_result.audio,
                    self.config.model,
                    self.config.language
                )
                
                if asr_result.text:
                    audio_duration = len(vad_result.audio) / self.config.sample_rate
                    segment = self.assembler.add_utterance(asr_result, audio_duration)
                    
                    # Send final transcript
                    message = {
                        "type": "transcript.final",
                        "text": segment.text,
                        "start": segment.start,
                        "end": segment.end,
                        "confidence": segment.confidence
                    }
                    
                    if self.config.word_timestamps:
                        message["words"] = [
                            {
                                "word": w.word,
                                "start": w.start,
                                "end": w.end,
                                "confidence": w.confidence
                            }
                            for w in segment.words
                        ]
                    
                    await self.send(message)
    
    async def handle_control(self, message: dict):
        """Handle control messages."""
        
        msg_type = message.get("type")
        
        if msg_type == "config":
            # Update configuration
            if "language" in message:
                self.config.language = message["language"]
        
        elif msg_type == "flush":
            # Force process any buffered audio
            if remaining := self.audio_buffer.flush():
                await self.process_chunk(remaining)
        
        elif msg_type == "end":
            # End session
            await self.send({
                "type": "session.end",
                "session_id": self.session_id,
                "total_duration": self.get_duration(),
                "total_speech_duration": self.assembler.current_time,
                "transcript": self.assembler.get_full_transcript(),
                "segments": [
                    {
                        "start": s.start,
                        "end": s.end,
                        "text": s.text
                    }
                    for s in self.assembler.get_all_segments()
                ]
            })
            
            await self.websocket.close()
    
    async def send(self, message: dict):
        """Send message to client."""
        await self.websocket.send(json.dumps(message))
    
    def get_duration(self) -> float:
        """Get session duration in seconds."""
        return time.time() - self.started_at
```

---

## Engine Metadata

```yaml
# engines/realtime/whisper-streaming/engine.yaml

id: whisper-streaming
type: realtime
name: Whisper Streaming
version: 1.0.0
description: |
  Real-time streaming transcription using Whisper models.
  Supports multiple model variants (fast/accurate).

container:
  gpu: required
  memory: 16G

capabilities:
  models:
    - fast       # distil-whisper
    - accurate   # faster-whisper large-v3
  languages:
    - all
  max_sessions: 4
  streaming: true
  word_timestamps: true

config_schema:
  type: object
  properties:
    model:
      type: string
      enum: [fast, accurate]
      default: fast
    language:
      type: string
      default: auto
    interim_results:
      type: boolean
      default: true
    word_timestamps:
      type: boolean
      default: false
    enable_vad:
      type: boolean
      default: true
```

---

## Dockerfile

```dockerfile
# engines/realtime/whisper-streaming/Dockerfile

FROM nvidia/cuda:12.1-runtime-ubuntu22.04

# Install Python and dependencies
RUN apt-get update && apt-get install -y \
    python3.11 \
    python3-pip \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Install Python packages
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy SDK
COPY dalston_realtime_sdk /app/dalston_realtime_sdk

# Copy engine
COPY engine.yaml /app/
COPY engine.py /app/

# Pre-download models
ENV HF_HOME=/models
RUN python3 -c "from faster_whisper import WhisperModel; WhisperModel('distil-whisper/distil-large-v3')"
RUN python3 -c "from faster_whisper import WhisperModel; WhisperModel('large-v3')"

# Download Silero VAD
RUN python3 -c "import torch; torch.hub.load('snakers4/silero-vad', 'silero_vad')"

WORKDIR /app
EXPOSE 9000

CMD ["python3", "engine.py"]
```

---

## Requirements

```
# engines/realtime/whisper-streaming/requirements.txt

faster-whisper>=1.0.0
torch>=2.0.0
websockets>=12.0
numpy>=1.24.0
redis>=5.0.0
silero-vad>=4.0.0
```

---

## Docker Compose Entry

```yaml
# In docker-compose.yml

realtime-whisper-1:
  build:
    context: ./engines/realtime/whisper-streaming
  ports:
    - "9001:9000"
  environment:
    - REDIS_URL=redis://redis:6379
    - WORKER_ID=realtime-whisper-1
    - WORKER_PORT=9000
    - MAX_SESSIONS=4
  volumes:
    - dalston-models:/models
    - dalston-sessions:/data/sessions
  deploy:
    resources:
      reservations:
        devices:
          - driver: nvidia
            count: 1
            capabilities: [gpu]
  restart: unless-stopped
  healthcheck:
    test: ["CMD", "curl", "-f", "http://localhost:9000/health"]
    interval: 30s
    timeout: 10s
    retries: 3
```

---

## Scaling

### Horizontal Scaling

Add more workers:

```yaml
realtime-whisper-2:
  # Same as realtime-whisper-1
  environment:
    - WORKER_ID=realtime-whisper-2
  ports:
    - "9002:9000"

realtime-whisper-3:
  environment:
    - WORKER_ID=realtime-whisper-3
  ports:
    - "9003:9000"
```

### GPU Assignment

Assign specific GPUs to workers:

```yaml
realtime-whisper-1:
  deploy:
    resources:
      reservations:
        devices:
          - driver: nvidia
            device_ids: ['0']
            capabilities: [gpu]

realtime-whisper-2:
  deploy:
    resources:
      reservations:
        devices:
          - driver: nvidia
            device_ids: ['1']
            capabilities: [gpu]
```
