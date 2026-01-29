# M8: ElevenLabs Compatibility Layer

| | |
|---|---|
| **Goal** | Drop-in replacement for ElevenLabs Speech-to-Text API |
| **Duration** | 2-3 days |
| **Dependencies** | M6 complete (real-time working) |
| **Deliverable** | ElevenLabs clients work unchanged |

## User Story

> *"As a developer using ElevenLabs, I can switch to Dalston by just changing the base URL."*

---

## ElevenLabs API Reference

We implement compatibility with:
- `POST /v1/speech-to-text` — Batch transcription
- `GET /v1/speech-to-text/transcripts/{id}` — Get result
- `WS /v1/speech-to-text/realtime` — Streaming

---

## Steps

### 8.1: Batch Endpoint

```python
# gateway/api/v1/elevenlabs.py

from fastapi import APIRouter, UploadFile, File, Form, HTTPException

router = APIRouter(tags=["ElevenLabs Compatibility"])

@router.post("/v1/speech-to-text")
async def elevenlabs_transcribe(
    file: UploadFile | None = File(None),
    cloud_storage_url: str | None = Form(None),
    model_id: str = Form("scribe_v1"),
    language_code: str | None = Form(None),
    diarize: bool = Form(False),
    num_speakers: int | None = Form(None),
    timestamps_granularity: str = Form("word"),
    tag_audio_events: bool = Form(False),
    keyterms: str | None = Form(None),  # JSON array
    webhook: bool = Form(False),
    webhook_id: str | None = Form(None),
):
    """ElevenLabs-compatible transcription endpoint."""
    
    # Map ElevenLabs params to Dalston
    dalston_params = {
        "language": language_code,
        "speaker_detection": "diarize" if diarize else "none",
        "num_speakers": num_speakers,
        "word_timestamps": timestamps_granularity in ("word", "character"),
        "detect_events": tag_audio_events,
        "prompt": " ".join(json.loads(keyterms)) if keyterms else None,
    }
    
    # Handle input
    if file:
        audio_source = file
    elif cloud_storage_url:
        audio_source = await download_from_url(cloud_storage_url)
    else:
        raise HTTPException(400, "Either file or cloud_storage_url required")
    
    # Create internal job
    job = await create_job(audio_source, dalston_params)
    
    # Async mode (webhook)
    if webhook:
        return {
            "message": "Transcription submitted",
            "request_id": f"req_{uuid4().hex[:12]}",
            "transcription_id": job.id
        }
    
    # Sync mode - wait for result
    result = await wait_for_completion(job.id, timeout=300)
    return format_elevenlabs_response(result)


@router.get("/v1/speech-to-text/transcripts/{transcription_id}")
async def elevenlabs_get_transcript(transcription_id: str):
    """ElevenLabs-compatible get transcript."""
    
    job = await get_job(transcription_id)
    
    if job.status == "pending":
        return {
            "transcription_id": transcription_id,
            "status": "pending"
        }
    
    if job.status == "running":
        return {
            "transcription_id": transcription_id,
            "status": "processing",
            "progress_percent": calculate_progress(job),
            "stage": get_current_stage(job)
        }
    
    if job.status == "failed":
        return {
            "transcription_id": transcription_id,
            "status": "failed",
            "error": job.error
        }
    
    return format_elevenlabs_response(job)


def format_elevenlabs_response(job: Job) -> dict:
    """Convert Dalston transcript to ElevenLabs format."""
    
    transcript = job.transcript
    
    # Build words array
    words = []
    for seg in transcript.get("segments", []):
        for word in seg.get("words") or []:
            words.append({
                "text": word["word"],
                "start": word["start"],
                "end": word["end"],
                "type": "word",
                "speaker_id": f"speaker_{seg['speaker'][-1]}" if seg.get("speaker") else None,
                "logprob": (word.get("confidence", 1.0) - 1) * 5  # Approximate conversion
            })
    
    return {
        "transcription_id": job.id,
        "status": "completed",
        "language_code": transcript.get("metadata", {}).get("language", "en"),
        "audio_duration": transcript.get("metadata", {}).get("audio_duration"),
        "text": transcript.get("text", ""),
        "words": words
    }
```

---

### 8.2: WebSocket Endpoint

```python
# gateway/api/v1/elevenlabs_realtime.py

@router.websocket("/v1/speech-to-text/realtime")
async def elevenlabs_realtime(
    websocket: WebSocket,
    model_id: str = Query("scribe_v1"),
    language_code: str = Query("auto"),
    commit_strategy: str = Query("vad"),
    include_timestamps: bool = Query(False),
):
    """ElevenLabs-compatible WebSocket endpoint."""
    
    # Map to Dalston params
    model = "fast" if model_id == "scribe_v1" else "accurate"
    
    await websocket.accept()
    
    allocation = await session_router.acquire_worker(
        language=language_code,
        model=model,
        client_ip=websocket.client.host
    )
    
    if not allocation:
        await websocket.send_json({
            "message_type": "error",
            "error_code": "capacity_exceeded",
            "error_message": "No workers available"
        })
        await websocket.close()
        return
    
    try:
        async with websockets.connect(f"{allocation.endpoint}/session") as worker_ws:
            # Send Dalston config
            await worker_ws.send(json.dumps({
                "session_id": allocation.session_id,
                "language": language_code,
                "model": model,
                "word_timestamps": include_timestamps,
                "interim_results": True,
                "enable_vad": commit_strategy == "vad"
            }))
            
            # Proxy with protocol translation
            await asyncio.gather(
                elevenlabs_client_to_dalston(websocket, worker_ws),
                dalston_to_elevenlabs_client(worker_ws, websocket, include_timestamps)
            )
    finally:
        await session_router.release_worker(allocation.session_id)
```

---

### 8.3: Protocol Translation

```python
# gateway/api/v1/elevenlabs_realtime.py

async def elevenlabs_client_to_dalston(client_ws: WebSocket, worker_ws):
    """Translate ElevenLabs input to Dalston format."""
    
    try:
        while True:
            data = await client_ws.receive_json()
            msg_type = data.get("message_type")
            
            if msg_type == "input_audio_chunk":
                # Decode base64 audio and send as binary
                audio_bytes = base64.b64decode(data["audio_base_64"])
                await worker_ws.send(audio_bytes)
                
                # Handle manual commit
                if data.get("commit"):
                    await worker_ws.send(json.dumps({"type": "flush"}))
            
            elif msg_type == "close_connection":
                await worker_ws.send(json.dumps({"type": "end"}))
                break
    
    except WebSocketDisconnect:
        await worker_ws.close()


async def dalston_to_elevenlabs_client(worker_ws, client_ws: WebSocket, include_timestamps: bool):
    """Translate Dalston output to ElevenLabs format."""
    
    try:
        async for message in worker_ws:
            data = json.loads(message)
            dalston_type = data.get("type")
            
            if dalston_type == "session.begin":
                # ElevenLabs doesn't have explicit session begin
                pass
            
            elif dalston_type == "transcript.partial":
                await client_ws.send_json({
                    "message_type": "partial_transcript",
                    "text": data["text"]
                })
            
            elif dalston_type == "transcript.final":
                if include_timestamps and data.get("words"):
                    await client_ws.send_json({
                        "message_type": "committed_transcript_with_timestamps",
                        "text": data["text"],
                        "words": [
                            {
                                "text": w["word"],
                                "start": w["start"],
                                "end": w["end"],
                                "type": "word",
                                "speaker_id": "speaker_1"
                            }
                            for w in data["words"]
                        ]
                    })
                else:
                    await client_ws.send_json({
                        "message_type": "committed_transcript",
                        "text": data["text"]
                    })
            
            elif dalston_type == "vad.speech_start":
                await client_ws.send_json({
                    "message_type": "begin_utterance",
                    "timestamp": data["timestamp"]
                })
            
            elif dalston_type == "vad.speech_end":
                await client_ws.send_json({
                    "message_type": "end_utterance",
                    "timestamp": data["timestamp"]
                })
            
            elif dalston_type == "session.end":
                await client_ws.send_json({
                    "message_type": "session_ended",
                    "transcript": data.get("transcript", "")
                })
            
            elif dalston_type == "error":
                await client_ws.send_json({
                    "message_type": "error",
                    "error_code": data.get("code", "unknown"),
                    "error_message": data.get("message", "Unknown error")
                })
    
    except websockets.ConnectionClosed:
        pass
```

---

### 8.4: ElevenLabs WebSocket Protocol Reference

#### Client → Server (ElevenLabs format)

```json
// Send audio chunk
{
    "message_type": "input_audio_chunk",
    "audio_base_64": "BASE64_ENCODED_AUDIO",
    "commit": false
}

// Manual commit (force transcription)
{
    "message_type": "input_audio_chunk",
    "audio_base_64": "BASE64_ENCODED_AUDIO",
    "commit": true
}

// End session
{
    "message_type": "close_connection"
}
```

#### Server → Client (ElevenLabs format)

```json
// Partial transcript
{
    "message_type": "partial_transcript",
    "text": "Hello"
}

// Final transcript (without timestamps)
{
    "message_type": "committed_transcript",
    "text": "Hello world"
}

// Final transcript (with timestamps)
{
    "message_type": "committed_transcript_with_timestamps",
    "text": "Hello world",
    "words": [
        {"text": "Hello", "start": 0.0, "end": 0.5, "type": "word"},
        {"text": "world", "start": 0.6, "end": 1.0, "type": "word"}
    ]
}

// VAD events
{
    "message_type": "begin_utterance",
    "timestamp": 1.5
}

{
    "message_type": "end_utterance", 
    "timestamp": 3.2
}

// Error
{
    "message_type": "error",
    "error_code": "capacity_exceeded",
    "error_message": "No workers available"
}
```

---

## Verification

### Test with ElevenLabs SDK

```python
# Using official ElevenLabs Python SDK
from elevenlabs import ElevenLabs

# Point to Dalston instead of ElevenLabs
client = ElevenLabs(
    api_key="not-needed-for-dalston",
    base_url="http://localhost:8000"  # Dalston!
)

# Batch transcription
result = client.speech_to_text.convert(
    file=open("audio.mp3", "rb"),
    model_id="scribe_v1",
    diarize=True
)

print(result.text)
print(result.words)
```

### Test WebSocket

```python
# Using ElevenLabs SDK for realtime
from elevenlabs import ElevenLabs

client = ElevenLabs(base_url="http://localhost:8000")

async def transcribe_stream():
    async with client.speech_to_text.realtime(
        model_id="scribe_v1",
        language_code="en"
    ) as session:
        # Send audio chunks
        for chunk in audio_chunks:
            await session.send(chunk)
        
        # Receive transcripts
        async for message in session:
            print(message)

asyncio.run(transcribe_stream())
```

---

## Checkpoint

✓ **POST /v1/speech-to-text** matches ElevenLabs API  
✓ **GET /v1/speech-to-text/transcripts/{id}** returns ElevenLabs format  
✓ **WS /v1/speech-to-text/realtime** uses ElevenLabs protocol  
✓ **Protocol translation** is bidirectional  
✓ **ElevenLabs SDK** works unchanged  

**Next**: [M9: Enrichment](M09-enrichment.md) — Emotions, events, LLM cleanup
