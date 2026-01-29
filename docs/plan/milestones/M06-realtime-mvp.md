# M6: Real-Time MVP

| | |
|---|---|
| **Goal** | Stream audio, get live transcripts |
| **Duration** | 5-6 days |
| **Dependencies** | M2 complete (can start parallel with M3-M5) |
| **Deliverable** | WebSocket streaming transcription |

## User Story

> *"As a user on a live call, I see text appearing as people speak."*

---

## Architecture Overview

```
┌─────────┐     ┌─────────┐     ┌────────────────┐     ┌──────────────┐
│ Client  │────▶│ Gateway │────▶│ Session Router │────▶│ RT Worker 1  │
│         │◀────│         │◀────│                │     │ (4 sessions) │
└─────────┘     └─────────┘     │                │     └──────────────┘
   WebSocket       Proxy        │                │     ┌──────────────┐
                                │                │────▶│ RT Worker 2  │
                                └────────────────┘     │ (4 sessions) │
                                                       └──────────────┘
```

---

## Steps

### 6.1: Realtime SDK

```
dalston/realtime_sdk/
├── __init__.py
├── engine.py          # Base RealtimeEngine class
├── session.py         # Session handler
├── vad.py             # Silero VAD wrapper
├── assembler.py       # Transcript assembly
├── registry.py        # Worker registry client
└── protocol.py        # Message types
```

---

### 6.2: Session Router

```
dalston/session_router/
├── __init__.py
├── router.py
├── registry.py
├── allocator.py
└── health.py
```

```python
# router.py
class SessionRouter:
    def __init__(self, redis: Redis):
        self.redis = redis
    
    async def acquire_worker(
        self,
        language: str,
        model: str,
        client_ip: str
    ) -> WorkerAllocation | None:
        """Find available worker and reserve a session slot."""
        
        worker_ids = await self.redis.smembers("dalston:realtime:workers")
        
        candidates = []
        for worker_id in worker_ids:
            worker = await self.redis.hgetall(f"dalston:realtime:worker:{worker_id}")
            if not worker:
                continue
            
            # Check status
            if worker.get("status") not in ("ready", "busy"):
                continue
            
            # Check capacity
            capacity = int(worker.get("capacity", 0))
            active = int(worker.get("active_sessions", 0))
            if active >= capacity:
                continue
            
            # Check model support
            models = json.loads(worker.get("models_loaded", "[]"))
            if model not in models:
                continue
            
            candidates.append({
                "worker_id": worker_id,
                "worker": worker,
                "available": capacity - active
            })
        
        if not candidates:
            return None
        
        # Select least loaded worker
        best = max(candidates, key=lambda c: c["available"])
        
        # Reserve slot atomically
        session_id = f"sess_{uuid4().hex[:12]}"
        
        pipe = self.redis.pipeline()
        pipe.hincrby(f"dalston:realtime:worker:{best['worker_id']}", "active_sessions", 1)
        pipe.hset(f"dalston:realtime:session:{session_id}", mapping={
            "worker_id": best["worker_id"],
            "status": "active",
            "language": language,
            "model": model,
            "client_ip": client_ip,
            "started_at": datetime.utcnow().isoformat()
        })
        await pipe.execute()
        
        return WorkerAllocation(
            worker_id=best["worker_id"],
            endpoint=best["worker"]["endpoint"],
            session_id=session_id
        )
    
    async def release_worker(self, session_id: str):
        """Release session slot."""
        session = await self.redis.hgetall(f"dalston:realtime:session:{session_id}")
        if session:
            pipe = self.redis.pipeline()
            pipe.hincrby(f"dalston:realtime:worker:{session['worker_id']}", "active_sessions", -1)
            pipe.hset(f"dalston:realtime:session:{session_id}", "status", "ended")
            pipe.hset(f"dalston:realtime:session:{session_id}", "ended_at", datetime.utcnow().isoformat())
            await pipe.execute()
```

---

### 6.3: Gateway WebSocket Endpoint

```python
# gateway/api/v1/realtime.py

@router.websocket("/v1/audio/transcriptions/stream")
async def realtime_transcription(
    websocket: WebSocket,
    language: str = Query("auto"),
    model: str = Query("fast"),
    word_timestamps: bool = Query(False),
    interim_results: bool = Query(True),
):
    await websocket.accept()
    
    # Acquire worker
    allocation = await session_router.acquire_worker(
        language=language,
        model=model,
        client_ip=websocket.client.host
    )
    
    if allocation is None:
        await websocket.send_json({
            "type": "error",
            "code": "no_capacity",
            "message": "No workers available. Try again later."
        })
        await websocket.close(code=1013)  # Try Again Later
        return
    
    try:
        # Connect to worker
        async with websockets.connect(f"{allocation.endpoint}/session") as worker_ws:
            # Send session config
            await worker_ws.send(json.dumps({
                "session_id": allocation.session_id,
                "language": language,
                "model": model,
                "word_timestamps": word_timestamps,
                "interim_results": interim_results
            }))
            
            # Bidirectional proxy
            await asyncio.gather(
                proxy_client_to_worker(websocket, worker_ws),
                proxy_worker_to_client(worker_ws, websocket)
            )
    
    except websockets.ConnectionClosed:
        pass
    
    finally:
        await session_router.release_worker(allocation.session_id)


async def proxy_client_to_worker(client_ws: WebSocket, worker_ws):
    """Forward audio from client to worker."""
    try:
        while True:
            data = await client_ws.receive()
            if data["type"] == "websocket.receive":
                if "bytes" in data:
                    await worker_ws.send(data["bytes"])
                elif "text" in data:
                    await worker_ws.send(data["text"])
    except WebSocketDisconnect:
        await worker_ws.close()


async def proxy_worker_to_client(worker_ws, client_ws: WebSocket):
    """Forward transcripts from worker to client."""
    try:
        async for message in worker_ws:
            await client_ws.send_text(message)
    except websockets.ConnectionClosed:
        pass
```

---

### 6.4: Realtime Worker Engine

```
engines/realtime/whisper-streaming/
├── Dockerfile
├── requirements.txt
├── engine.yaml
└── engine.py
```

```python
# engine.py
import asyncio
import websockets
import torch
from faster_whisper import WhisperModel

class RealtimeWhisperEngine:
    def __init__(self):
        self.worker_id = os.environ["WORKER_ID"]
        self.port = int(os.environ.get("WORKER_PORT", 9000))
        self.max_sessions = int(os.environ.get("MAX_SESSIONS", 4))
        self.sessions: dict[str, SessionHandler] = {}
        self.models = {}
        self.vad_model = None
    
    async def start(self):
        # Load models
        print("Loading models...")
        self.models["fast"] = WhisperModel(
            "distil-whisper/distil-large-v3",
            device="cuda",
            compute_type="float16"
        )
        self.models["accurate"] = WhisperModel(
            "large-v3",
            device="cuda", 
            compute_type="float16"
        )
        
        # Load VAD
        self.vad_model, _ = torch.hub.load(
            repo_or_dir='snakers4/silero-vad',
            model='silero_vad'
        )
        
        # Register with session router
        await self._register()
        
        # Start heartbeat
        asyncio.create_task(self._heartbeat_loop())
        
        # Start WebSocket server
        print(f"Starting WebSocket server on port {self.port}...")
        async with websockets.serve(self._handle_connection, "0.0.0.0", self.port):
            await asyncio.Future()
    
    async def _handle_connection(self, websocket, path):
        # Receive config
        config_msg = await websocket.recv()
        config = json.loads(config_msg)
        
        session = SessionHandler(
            session_id=config["session_id"],
            websocket=websocket,
            model=self.models[config.get("model", "fast")],
            vad_model=self.vad_model,
            config=config
        )
        
        self.sessions[config["session_id"]] = session
        
        try:
            await session.run()
        finally:
            del self.sessions[config["session_id"]]
    
    async def _register(self):
        redis = Redis.from_url(os.environ["REDIS_URL"])
        await redis.sadd("dalston:realtime:workers", self.worker_id)
        await redis.hset(f"dalston:realtime:worker:{self.worker_id}", mapping={
            "endpoint": f"ws://{self.worker_id}:{self.port}",
            "status": "ready",
            "capacity": self.max_sessions,
            "active_sessions": 0,
            "models_loaded": json.dumps(["fast", "accurate"]),
            "last_heartbeat": datetime.utcnow().isoformat()
        })
    
    async def _heartbeat_loop(self):
        redis = Redis.from_url(os.environ["REDIS_URL"])
        while True:
            await redis.hset(f"dalston:realtime:worker:{self.worker_id}", mapping={
                "active_sessions": len(self.sessions),
                "last_heartbeat": datetime.utcnow().isoformat()
            })
            await asyncio.sleep(10)


if __name__ == "__main__":
    engine = RealtimeWhisperEngine()
    asyncio.run(engine.start())
```

---

### 6.5: Session Handler

```python
# realtime_sdk/session.py

class SessionHandler:
    def __init__(self, session_id, websocket, model, vad_model, config):
        self.session_id = session_id
        self.websocket = websocket
        self.model = model
        self.vad = VADProcessor(vad_model)
        self.config = config
        self.assembler = TranscriptAssembler()
        self.audio_time = 0.0
        self.sample_rate = 16000
        self.last_partial_time = 0.0
    
    async def run(self):
        # Send session start
        await self._send({
            "type": "session.begin",
            "session_id": self.session_id
        })
        
        try:
            async for message in self.websocket:
                if isinstance(message, bytes):
                    await self._handle_audio(message)
                else:
                    data = json.loads(message)
                    if data.get("type") == "end":
                        break
                    elif data.get("type") == "flush":
                        await self._flush_pending()
        finally:
            await self._send_session_end()
    
    async def _handle_audio(self, audio_bytes: bytes):
        # Convert to float32
        audio = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0
        
        # Update time
        chunk_duration = len(audio) / self.sample_rate
        
        # VAD processing
        vad_result = self.vad.process_chunk(audio)
        
        if vad_result.event == VADEvent.SPEECH_START:
            await self._send({
                "type": "vad.speech_start",
                "timestamp": self.audio_time
            })
        
        elif vad_result.event == VADEvent.SPEECH_END:
            await self._send({
                "type": "vad.speech_end", 
                "timestamp": self.audio_time
            })
            
            # Transcribe accumulated speech
            if vad_result.audio is not None and len(vad_result.audio) > 0:
                transcript = await self._transcribe(vad_result.audio, vad_result.start_time)
                await self._send_final(transcript)
        
        elif vad_result.event == VADEvent.SPEECH_CONTINUE:
            # Emit partial results periodically
            if self.config.get("interim_results") and self._should_emit_partial():
                partial = await self._transcribe_partial()
                if partial:
                    await self._send({
                        "type": "transcript.partial",
                        "text": partial
                    })
        
        self.audio_time += chunk_duration
    
    async def _transcribe(self, audio: np.ndarray, start_time: float) -> dict:
        segments, info = self.model.transcribe(
            audio,
            language=None if self.config.get("language") == "auto" else self.config.get("language"),
            beam_size=3,
            vad_filter=False  # Already did VAD
        )
        
        text_parts = []
        words = []
        
        for seg in segments:
            text_parts.append(seg.text.strip())
            
            if self.config.get("word_timestamps") and seg.words:
                for w in seg.words:
                    words.append({
                        "word": w.word,
                        "start": start_time + w.start,
                        "end": start_time + w.end,
                        "confidence": w.probability
                    })
        
        text = " ".join(text_parts)
        self.assembler.add_segment(text, start_time, self.audio_time)
        
        return {
            "text": text,
            "start": start_time,
            "end": self.audio_time,
            "words": words if words else None
        }
    
    async def _send_final(self, transcript: dict):
        await self._send({
            "type": "transcript.final",
            **transcript
        })
    
    async def _send_session_end(self):
        await self._send({
            "type": "session.end",
            "session_id": self.session_id,
            "total_duration": self.audio_time,
            "transcript": self.assembler.get_full_text()
        })
    
    async def _send(self, message: dict):
        await self.websocket.send(json.dumps(message))
    
    def _should_emit_partial(self) -> bool:
        # Emit partial every 500ms of audio
        if self.audio_time - self.last_partial_time > 0.5:
            self.last_partial_time = self.audio_time
            return True
        return False
```

---

### 6.6: VAD Processor

```python
# realtime_sdk/vad.py

class VADEvent(Enum):
    NONE = "none"
    SPEECH_START = "speech_start"
    SPEECH_CONTINUE = "speech_continue"
    SPEECH_END = "speech_end"

@dataclass
class VADResult:
    event: VADEvent
    audio: np.ndarray | None = None
    start_time: float | None = None

class VADProcessor:
    def __init__(self, model, threshold: float = 0.5, min_speech_ms: int = 250, min_silence_ms: int = 500):
        self.model = model
        self.threshold = threshold
        self.min_speech_samples = int(16000 * min_speech_ms / 1000)
        self.min_silence_samples = int(16000 * min_silence_ms / 1000)
        
        self.buffer = []
        self.speech_buffer = []
        self.is_speaking = False
        self.silence_samples = 0
        self.speech_start_time = None
        self.current_time = 0.0
    
    def process_chunk(self, audio: np.ndarray) -> VADResult:
        chunk_duration = len(audio) / 16000
        
        # Run VAD
        speech_prob = self.model(torch.from_numpy(audio), 16000).item()
        is_speech = speech_prob > self.threshold
        
        result = VADResult(event=VADEvent.NONE)
        
        if is_speech:
            self.silence_samples = 0
            
            if not self.is_speaking:
                # Speech started
                self.is_speaking = True
                self.speech_start_time = self.current_time
                self.speech_buffer = list(self.buffer)  # Include lookback
                result = VADResult(event=VADEvent.SPEECH_START)
            else:
                result = VADResult(event=VADEvent.SPEECH_CONTINUE)
            
            self.speech_buffer.append(audio)
        
        else:
            if self.is_speaking:
                self.silence_samples += len(audio)
                self.speech_buffer.append(audio)  # Include trailing silence
                
                if self.silence_samples >= self.min_silence_samples:
                    # Speech ended
                    self.is_speaking = False
                    speech_audio = np.concatenate(self.speech_buffer)
                    result = VADResult(
                        event=VADEvent.SPEECH_END,
                        audio=speech_audio,
                        start_time=self.speech_start_time
                    )
                    self.speech_buffer = []
                else:
                    result = VADResult(event=VADEvent.SPEECH_CONTINUE)
        
        # Keep lookback buffer
        self.buffer.append(audio)
        if len(self.buffer) > 3:  # ~300ms lookback
            self.buffer.pop(0)
        
        self.current_time += chunk_duration
        return result
    
    def get_buffer(self) -> np.ndarray | None:
        if self.speech_buffer:
            return np.concatenate(self.speech_buffer)
        return None
```

---

## WebSocket Protocol

### Client → Server

| Type | Format | Description |
|------|--------|-------------|
| Audio | Binary | Raw PCM int16 @ 16kHz mono |
| End | `{"type": "end"}` | End session gracefully |
| Flush | `{"type": "flush"}` | Force transcription of buffered audio |

### Server → Client

| Type | Example | Description |
|------|---------|-------------|
| session.begin | `{"type": "session.begin", "session_id": "..."}` | Session started |
| vad.speech_start | `{"type": "vad.speech_start", "timestamp": 1.5}` | Speech detected |
| vad.speech_end | `{"type": "vad.speech_end", "timestamp": 3.2}` | Speech ended |
| transcript.partial | `{"type": "transcript.partial", "text": "Hello"}` | Interim result |
| transcript.final | `{"type": "transcript.final", "text": "Hello world", "start": 1.5, "end": 3.2}` | Confirmed |
| session.end | `{"type": "session.end", "transcript": "..."}` | Session complete |
| error | `{"type": "error", "code": "...", "message": "..."}` | Error |

---

## Verification

```python
# Test client
import asyncio
import websockets
import numpy as np

async def test_realtime():
    uri = "ws://localhost:8000/v1/audio/transcriptions/stream?model=fast"
    
    async with websockets.connect(uri) as ws:
        # Generate test audio (sine wave)
        duration = 3.0
        t = np.linspace(0, duration, int(16000 * duration))
        audio = (np.sin(2 * np.pi * 440 * t) * 32767).astype(np.int16)
        
        # Send in chunks
        chunk_size = 1600  # 100ms
        for i in range(0, len(audio), chunk_size):
            chunk = audio[i:i+chunk_size]
            await ws.send(chunk.tobytes())
            await asyncio.sleep(0.1)
        
        # End session
        await ws.send('{"type": "end"}')
        
        # Receive messages
        async for msg in ws:
            print(json.loads(msg))

asyncio.run(test_realtime())
```

---

## Checkpoint

✓ **Session Router** manages worker pool  
✓ **Gateway** proxies WebSocket to workers  
✓ **Worker** handles multiple concurrent sessions  
✓ **VAD** detects speech boundaries  
✓ **Streaming ASR** produces partial + final results  

**Next**: [M7: Hybrid Mode](M07-hybrid-mode.md) — Real-time + batch enhancement
