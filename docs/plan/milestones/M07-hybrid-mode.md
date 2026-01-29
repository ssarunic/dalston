# M7: Hybrid Mode

| | |
|---|---|
| **Goal** | Real-time results + batch enhancement |
| **Duration** | 2-3 days |
| **Dependencies** | M6 complete |
| **Deliverable** | Sessions can trigger batch enhancement on end |

## User Story

> *"I see text live, then get improved results with speaker names afterward."*

---

## Overview

```
┌──────────────────────────────────────────────────────────────────────┐
│                                                                       │
│   REALTIME SESSION                        BATCH ENHANCEMENT          │
│                                                                       │
│   Audio ───▶ Realtime ───▶ Immediate     Session ───▶ Batch ───▶    │
│   stream     Worker       transcript     recording   Pipeline        │
│                                │                        │            │
│                                ▼                        ▼            │
│                           User sees               Enhanced result    │
│                           text NOW                + diarization      │
│                           (< 500ms)               + speaker names    │
│                                                   + LLM cleanup      │
│                                                                       │
└──────────────────────────────────────────────────────────────────────┘
```

---

## Steps

### 7.1: Session Recording

Add optional recording to session handler:

```python
# realtime_sdk/session.py

class SessionHandler:
    def __init__(self, ..., enhance_on_end: bool = False):
        ...
        self.enhance_on_end = enhance_on_end
        self.recorder = SessionRecorder(session_id) if enhance_on_end else None
    
    async def _handle_audio(self, audio_bytes: bytes):
        # Record all audio if enhancement requested
        if self.recorder:
            self.recorder.write(audio_bytes)
        
        # ... rest of audio processing unchanged
    
    async def _send_session_end(self):
        enhancement_job_id = None
        
        if self.recorder:
            # Finalize recording
            audio_path = await self.recorder.finalize()
            
            # Create batch enhancement job
            enhancement_job_id = await self._create_enhancement_job(audio_path)
        
        await self._send({
            "type": "session.end",
            "session_id": self.session_id,
            "total_duration": self.audio_time,
            "transcript": self.assembler.get_full_text(),
            "enhancement_job_id": enhancement_job_id  # NEW
        })


class SessionRecorder:
    def __init__(self, session_id: str):
        self.session_id = session_id
        self.output_dir = Path(f"/data/sessions/{session_id}")
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.wav_path = self.output_dir / "audio.wav"
        self.chunks: list[bytes] = []
    
    def write(self, audio_bytes: bytes):
        self.chunks.append(audio_bytes)
    
    async def finalize(self) -> str:
        """Write accumulated audio to WAV file."""
        if not self.chunks:
            return None
        
        # Concatenate all chunks
        all_audio = b"".join(self.chunks)
        audio_array = np.frombuffer(all_audio, dtype=np.int16)
        
        # Write WAV
        import soundfile as sf
        sf.write(str(self.wav_path), audio_array, 16000)
        
        return str(self.wav_path)
```

---

### 7.2: Enhancement Job Creation

```python
# realtime_sdk/session.py

class SessionHandler:
    async def _create_enhancement_job(self, audio_path: str) -> str:
        """Create batch transcription job from session recording."""
        
        job_id = f"job_{uuid4().hex[:12]}"
        
        job = {
            "id": job_id,
            "audio_path": audio_path,
            "status": "pending",
            "parameters": {
                "speaker_detection": "diarize",
                "word_timestamps": True,
                "llm_cleanup": self.config.get("enhance_llm_cleanup", False),
                "detect_emotions": self.config.get("enhance_emotions", False)
            },
            "source": "realtime_enhancement",
            "source_session_id": self.session_id,
            "created_at": datetime.utcnow().isoformat()
        }
        
        # Save job to Redis
        redis = Redis.from_url(os.environ["REDIS_URL"])
        await redis.set(f"dalston:job:{job_id}", json.dumps(job))
        
        # Trigger orchestrator
        await redis.publish("dalston:events", json.dumps({
            "type": "job.created",
            "job_id": job_id
        }))
        
        return job_id
```

---

### 7.3: Gateway Enhancement Parameter

```python
# gateway/api/v1/realtime.py

@router.websocket("/v1/audio/transcriptions/stream")
async def realtime_transcription(
    websocket: WebSocket,
    language: str = Query("auto"),
    model: str = Query("fast"),
    word_timestamps: bool = Query(False),
    interim_results: bool = Query(True),
    enhance_on_end: bool = Query(False),  # NEW
    enhance_llm_cleanup: bool = Query(False),  # NEW
    enhance_emotions: bool = Query(False),  # NEW
):
    await websocket.accept()
    
    allocation = await session_router.acquire_worker(...)
    
    if allocation is None:
        # ... error handling
        return
    
    try:
        async with websockets.connect(f"{allocation.endpoint}/session") as worker_ws:
            # Send config including enhancement options
            await worker_ws.send(json.dumps({
                "session_id": allocation.session_id,
                "language": language,
                "model": model,
                "word_timestamps": word_timestamps,
                "interim_results": interim_results,
                "enhance_on_end": enhance_on_end,  # NEW
                "enhance_llm_cleanup": enhance_llm_cleanup,
                "enhance_emotions": enhance_emotions
            }))
            
            # ... proxy as before
    finally:
        await session_router.release_worker(allocation.session_id)
```

---

### 7.4: Get Enhancement Status Endpoint

```python
# gateway/api/v1/realtime.py

@router.get("/v1/audio/transcriptions/stream/{session_id}/enhancement")
async def get_enhancement_status(session_id: str):
    """Get status of enhancement job for a session."""
    
    # Find session
    session = await redis.hgetall(f"dalston:realtime:session:{session_id}")
    if not session:
        raise HTTPException(404, "Session not found")
    
    # Check if enhancement was requested
    job_id = session.get("enhancement_job_id")
    if not job_id:
        return {"status": "not_requested"}
    
    # Get job status
    job = await get_job(job_id)
    
    return {
        "session_id": session_id,
        "enhancement_job_id": job_id,
        "status": job.status,
        "transcript": job.transcript if job.status == "completed" else None
    }
```

---

### 7.5: Link Session to Enhancement Job

Update session record when enhancement job is created:

```python
# realtime_sdk/session.py

async def _create_enhancement_job(self, audio_path: str) -> str:
    job_id = f"job_{uuid4().hex[:12]}"
    
    # ... create job as before
    
    # Link session to job
    redis = Redis.from_url(os.environ["REDIS_URL"])
    await redis.hset(f"dalston:realtime:session:{self.session_id}", 
                     "enhancement_job_id", job_id)
    
    return job_id
```

---

## Usage Flow

```
1. Client connects with enhance_on_end=true
   
   ws://localhost:8000/v1/audio/transcriptions/stream?enhance_on_end=true

2. Client streams audio, receives real-time transcripts

   ← {"type": "transcript.partial", "text": "Hello"}
   ← {"type": "transcript.final", "text": "Hello world", ...}

3. Client ends session

   → {"type": "end"}
   ← {"type": "session.end", 
       "transcript": "Hello world...",
       "enhancement_job_id": "job_abc123"}

4. Client polls for enhanced result

   GET /v1/audio/transcriptions/job_abc123
   
   → {"status": "completed", "segments": [...], "speakers": [...]}
```

---

## Verification

```python
# Test client with enhancement
async def test_hybrid():
    uri = "ws://localhost:8000/v1/audio/transcriptions/stream?enhance_on_end=true"
    
    async with websockets.connect(uri) as ws:
        # Stream audio
        for chunk in audio_chunks:
            await ws.send(chunk)
            msg = await ws.recv()
            print("Realtime:", json.loads(msg))
        
        # End session
        await ws.send('{"type": "end"}')
        
        # Get session end with job ID
        end_msg = await ws.recv()
        data = json.loads(end_msg)
        job_id = data.get("enhancement_job_id")
        print(f"Enhancement job: {job_id}")
    
    # Poll for enhanced result
    while True:
        resp = requests.get(f"http://localhost:8000/v1/audio/transcriptions/{job_id}")
        result = resp.json()
        
        if result["status"] == "completed":
            print("Enhanced transcript:", result["text"])
            print("Speakers:", result["speakers"])
            break
        
        await asyncio.sleep(2)
```

---

## Checkpoint

✓ **Session recording** saves all streamed audio  
✓ **Enhancement job** created automatically on session end  
✓ **Job linked** to session for status tracking  
✓ **Full batch pipeline** runs on recorded audio  
✓ **Client receives** both real-time and enhanced results  

**Next**: [M8: ElevenLabs Compatibility](M08-elevenlabs-compat.md) — Drop-in API replacement
