# M5: Export Formats & Webhooks

| | |
|---|---|
| **Goal** | Export transcripts in various formats, support async webhooks |
| **Duration** | 2 days |
| **Dependencies** | M4 complete |
| **Deliverable** | SRT/VTT downloads, webhook notifications |

## User Story

> *"As a user, I can download my transcript as an SRT file for subtitles."*

> *"As a developer, I receive a webhook when my transcription is complete."*

---

## Steps

### 5.1: Export Endpoints

```python
# gateway/api/v1/transcription.py

from fastapi.responses import Response

@router.get("/v1/audio/transcriptions/{job_id}/export/{format}")
async def export_transcript(
    job_id: str, 
    format: Literal["srt", "vtt", "txt", "json"],
    include_speakers: bool = Query(True),
    max_line_length: int = Query(42)
):
    job = await get_completed_job(job_id)
    if job.status != "completed":
        raise HTTPException(400, "Job not complete")
    
    transcript = job.transcript
    
    if format == "srt":
        content = generate_srt(transcript, include_speakers, max_line_length)
        return Response(
            content, 
            media_type="text/plain",
            headers={"Content-Disposition": f"attachment; filename={job_id}.srt"}
        )
    
    elif format in ("vtt", "webvtt"):
        content = generate_vtt(transcript, include_speakers, max_line_length)
        return Response(content, media_type="text/vtt")
    
    elif format == "txt":
        content = generate_txt(transcript, include_speakers)
        return Response(content, media_type="text/plain")
    
    elif format == "json":
        return transcript
    
    raise HTTPException(400, f"Unknown format: {format}")
```

---

### 5.2: Export Generators

```python
# gateway/services/export.py

def format_timestamp_srt(seconds: float) -> str:
    """Format as 00:00:01,500"""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds % 1) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

def format_timestamp_vtt(seconds: float) -> str:
    """Format as 00:00:01.500"""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds % 1) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"

def wrap_text(text: str, max_chars: int) -> str:
    """Word wrap for subtitles."""
    words = text.split()
    lines = []
    current_line = []
    current_length = 0
    
    for word in words:
        if current_length + len(word) + 1 > max_chars and current_line:
            lines.append(" ".join(current_line))
            current_line = [word]
            current_length = len(word)
        else:
            current_line.append(word)
            current_length += len(word) + 1
    
    if current_line:
        lines.append(" ".join(current_line))
    
    return "\n".join(lines)

def generate_srt(transcript: dict, include_speakers: bool, max_chars: int) -> str:
    lines = []
    for i, seg in enumerate(transcript["segments"], 1):
        start = format_timestamp_srt(seg["start"])
        end = format_timestamp_srt(seg["end"])
        
        text = seg["text"]
        if include_speakers and seg.get("speaker"):
            text = f"[{seg['speaker']}] {text}"
        
        wrapped = wrap_text(text, max_chars)
        lines.append(f"{i}\n{start} --> {end}\n{wrapped}\n")
    
    return "\n".join(lines)

def generate_vtt(transcript: dict, include_speakers: bool, max_chars: int) -> str:
    lines = ["WEBVTT", ""]
    
    for seg in transcript["segments"]:
        start = format_timestamp_vtt(seg["start"])
        end = format_timestamp_vtt(seg["end"])
        
        text = seg["text"]
        if include_speakers and seg.get("speaker"):
            text = f"<v {seg['speaker']}>{text}"
        
        wrapped = wrap_text(text, max_chars)
        lines.append(f"{start} --> {end}\n{wrapped}\n")
    
    return "\n".join(lines)

def generate_txt(transcript: dict, include_speakers: bool) -> str:
    lines = []
    current_speaker = None
    
    for seg in transcript["segments"]:
        speaker = seg.get("speaker")
        
        if include_speakers and speaker and speaker != current_speaker:
            lines.append(f"\n[{speaker}]")
            current_speaker = speaker
        
        lines.append(seg["text"])
    
    return " ".join(lines).strip()
```

---

### 5.3: Webhook Support

Update job creation to accept webhook URL:

```python
# gateway/api/v1/transcription.py

@router.post("/v1/audio/transcriptions")
async def create_transcription(
    file: UploadFile,
    webhook_url: str | None = Form(None),
    webhook_metadata: str | None = Form(None),  # JSON string
    # ... other params
):
    job = await create_job(
        file=file,
        webhook_url=webhook_url,
        webhook_metadata=json.loads(webhook_metadata) if webhook_metadata else None,
        # ...
    )
    
    return {"id": job.id, "status": "pending"}
```

---

### 5.4: Webhook Delivery

```python
# orchestrator/webhooks.py

import hmac
import hashlib
import httpx

async def send_webhook(job: Job):
    if not job.webhook_url:
        return
    
    payload = {
        "event": "transcription.completed" if job.status == "completed" else "transcription.failed",
        "transcription_id": job.id,
        "status": job.status,
        "timestamp": datetime.utcnow().isoformat(),
        "text": job.transcript.get("text") if job.status == "completed" else None,
        "duration": job.transcript.get("metadata", {}).get("audio_duration"),
        "webhook_metadata": job.webhook_metadata
    }
    
    # Generate signature
    timestamp = str(int(time.time()))
    payload_json = json.dumps(payload, sort_keys=True)
    signature = hmac.new(
        (job.webhook_secret or "").encode(),
        f"{timestamp}.{payload_json}".encode(),
        hashlib.sha256
    ).hexdigest()
    
    headers = {
        "Content-Type": "application/json",
        "X-Dalston-Signature": f"sha256={signature}",
        "X-Dalston-Timestamp": timestamp,
        "User-Agent": "Dalston-Webhook/1.0"
    }
    
    # Retry with exponential backoff
    for attempt in range(3):
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    job.webhook_url,
                    json=payload,
                    headers=headers,
                    timeout=10
                )
                
                if resp.status_code < 400:
                    logger.info(f"Webhook delivered: {job.id} → {job.webhook_url}")
                    return
                
                logger.warning(f"Webhook failed ({resp.status_code}): {job.id}")
        
        except Exception as e:
            logger.warning(f"Webhook error: {e}")
        
        # Wait before retry
        await asyncio.sleep(2 ** attempt)
    
    logger.error(f"Webhook exhausted retries: {job.id} → {job.webhook_url}")

# orchestrator/handlers.py
async def handle_job_completed(job_id: str):
    job = await load_job(job_id)
    
    if job.webhook_url:
        await send_webhook(job)
```

---

### 5.5: Webhook Verification (Client Side)

Document how clients verify webhooks:

```python
# Example client-side verification
import hmac
import hashlib

def verify_webhook(payload: bytes, signature: str, timestamp: str, secret: str) -> bool:
    expected = hmac.new(
        secret.encode(),
        f"{timestamp}.{payload.decode()}".encode(),
        hashlib.sha256
    ).hexdigest()
    
    received = signature.replace("sha256=", "")
    return hmac.compare_digest(expected, received)
```

---

## Verification

```bash
# Export as SRT
curl http://localhost:8000/v1/audio/transcriptions/job_xyz/export/srt \
  --output transcript.srt

cat transcript.srt
# 1
# 00:00:00,000 --> 00:00:02,500
# [SPEAKER_00] Welcome to the show.
#
# 2
# 00:00:02,800 --> 00:00:05,100
# [SPEAKER_01] Thanks for having me.

# Export as VTT
curl http://localhost:8000/v1/audio/transcriptions/job_xyz/export/vtt
# WEBVTT
#
# 00:00:00.000 --> 00:00:02.500
# <v SPEAKER_00>Welcome to the show.

# Submit with webhook
curl -X POST http://localhost:8000/v1/audio/transcriptions \
  -F "file=@audio.mp3" \
  -F "webhook_url=https://my-server.com/webhooks/dalston" \
  -F 'webhook_metadata={"user_id": "123"}'

# Webhook payload received:
# {
#   "event": "transcription.completed",
#   "transcription_id": "job_abc",
#   "text": "Welcome to...",
#   "webhook_metadata": {"user_id": "123"}
# }
```

---

## Checkpoint

✓ **SRT export** with proper timestamp format  
✓ **VTT export** with speaker voice tags  
✓ **TXT export** with speaker labels  
✓ **Webhooks** with HMAC signature  
✓ **Retry logic** for failed deliveries  

**Next**: [M6: Real-Time MVP](M06-realtime-mvp.md) — Stream audio, get live transcripts
