# Real-Time Session Persistence

## Overview

Real-time sessions are ephemeral by default - audio is buffered in memory, transcripts stream to clients, and session state expires shortly after disconnect. Session persistence adds durable storage for audit, recovery, and hybrid processing workflows.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                     SESSION PERSISTENCE ARCHITECTURE                             │
│                                                                                  │
│                                                                                  │
│   ┌─────────────────────────────────────────────────────────────────────────┐   │
│   │                           GATEWAY                                        │   │
│   │                                                                          │   │
│   │   WebSocket Handler                                                      │   │
│   │         │                                                                │   │
│   │         ├──▶ Create RealtimeSessionModel (PostgreSQL)                   │   │
│   │         │                                                                │   │
│   │         └──▶ Proxy to Worker                                            │   │
│   │                                                                          │   │
│   └─────────────────────────────────────────────────────────────────────────┘   │
│                                                                                  │
│   ┌─────────────────────────────────────────────────────────────────────────┐   │
│   │                        REALTIME WORKER                                   │   │
│   │                                                                          │   │
│   │   SessionHandler                                                         │   │
│   │         │                                                                │   │
│   │         ├──▶ AudioRecorder ──▶ S3 (buffered multipart upload)           │   │
│   │         │                                                                │   │
│   │         ├──▶ StatsTracker ──▶ PostgreSQL (periodic updates)             │   │
│   │         │                                                                │   │
│   │         └──▶ TranscriptAssembler ──▶ S3 (on session end)                │   │
│   │                                                                          │   │
│   └─────────────────────────────────────────────────────────────────────────┘   │
│                                                                                  │
└─────────────────────────────────────────────────────────────────────────────────┘
```

---

## Data Model

### PostgreSQL: RealtimeSessionModel

```sql
CREATE TABLE realtime_sessions (
    id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL REFERENCES tenants(id),

    -- Status
    status VARCHAR(20) NOT NULL DEFAULT 'active',  -- active, completed, error, interrupted

    -- Parameters (immutable)
    language VARCHAR(10),
    model VARCHAR(20),           -- fast, accurate
    encoding VARCHAR(20),        -- pcm_s16le, mulaw, etc.
    sample_rate INTEGER,

    -- Feature flags
    store_audio BOOLEAN DEFAULT FALSE,
    store_transcript BOOLEAN DEFAULT FALSE,
    enhance_on_end BOOLEAN DEFAULT FALSE,

    -- Results
    audio_uri TEXT,              -- s3://bucket/sessions/{id}/audio.wav
    transcript_uri TEXT,         -- s3://bucket/sessions/{id}/transcript.json
    enhancement_job_id UUID REFERENCES jobs(id),

    -- Stats (updated during session)
    audio_duration_seconds FLOAT DEFAULT 0,
    utterance_count INTEGER DEFAULT 0,
    word_count INTEGER DEFAULT 0,

    -- Tracking
    worker_id VARCHAR(100),
    client_ip VARCHAR(45),
    previous_session_id UUID,    -- For session resume/linking

    -- Timestamps
    started_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    ended_at TIMESTAMP WITH TIME ZONE,

    -- Error tracking
    error TEXT,

    -- Indexes
    CONSTRAINT fk_previous_session FOREIGN KEY (previous_session_id)
        REFERENCES realtime_sessions(id)
);

CREATE INDEX idx_realtime_sessions_tenant ON realtime_sessions(tenant_id);
CREATE INDEX idx_realtime_sessions_status ON realtime_sessions(status);
CREATE INDEX idx_realtime_sessions_started ON realtime_sessions(started_at);
```

### Redis: Active Session State

Redis continues to track active session state for real-time operations:

```
dalston:realtime:session:{session_id}        (Hash)
{
  "worker_id": "realtime-whisper-1",
  "status": "active",
  "language": "en",
  "model": "fast",
  "started_at": "2025-01-28T12:00:00Z",
  "audio_duration": 45.6,
  "store_audio": true,
  "store_transcript": true,
  "enhance_on_end": true
}
```

### S3: Audio and Transcript Storage

```
s3://{bucket}/
└── sessions/
    └── {session_id}/
        ├── audio.wav           # 16kHz, 16-bit PCM
        └── transcript.json     # Final assembled transcript
```

---

## WebSocket Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `store_audio` | bool | false | Record audio to S3 during session |
| `store_transcript` | bool | false | Save final transcript to S3 on end |
| `enhance_on_end` | bool | false | Create batch job on session end (requires store_audio) |
| `resume_session_id` | string | null | Link this session to a previous one |

**Example:**

```
ws://localhost:8000/v1/audio/transcriptions/stream?store_audio=true&store_transcript=true
```

---

## Audio Recording

### Buffered S3 Upload

Audio is recorded using S3 multipart upload to handle long sessions efficiently:

```python
class AudioRecorder:
    def __init__(self, session_id: str, s3_client, bucket: str):
        self.buffer = io.BytesIO()
        self.buffer_size = 0
        self.flush_threshold = 5 * 1024 * 1024  # 5MB
        self.upload_id = None
        self.parts = []

    async def start(self):
        # Initiate multipart upload
        response = await self.s3_client.create_multipart_upload(
            Bucket=self.bucket,
            Key=f"sessions/{self.session_id}/audio.raw"
        )
        self.upload_id = response["UploadId"]

    async def write(self, audio_chunk: bytes):
        self.buffer.write(audio_chunk)
        self.buffer_size += len(audio_chunk)

        if self.buffer_size >= self.flush_threshold:
            await self._flush_part()

    async def _flush_part(self):
        part_number = len(self.parts) + 1
        self.buffer.seek(0)

        response = await self.s3_client.upload_part(
            Bucket=self.bucket,
            Key=f"sessions/{self.session_id}/audio.raw",
            UploadId=self.upload_id,
            PartNumber=part_number,
            Body=self.buffer.read()
        )

        self.parts.append({
            "PartNumber": part_number,
            "ETag": response["ETag"]
        })

        self.buffer = io.BytesIO()
        self.buffer_size = 0

    async def finalize(self) -> str:
        # Flush remaining data
        if self.buffer_size > 0:
            await self._flush_part()

        # Complete multipart upload
        await self.s3_client.complete_multipart_upload(
            Bucket=self.bucket,
            Key=f"sessions/{self.session_id}/audio.raw",
            UploadId=self.upload_id,
            MultipartUpload={"Parts": self.parts}
        )

        # Convert to WAV (add header)
        await self._convert_to_wav()

        return f"s3://{self.bucket}/sessions/{self.session_id}/audio.wav"
```

### Error Handling

On unexpected disconnect:

1. Attempt to complete multipart upload with available parts
2. Mark session as `interrupted` in PostgreSQL
3. Set `audio_uri` if partial recording is usable

---

## Transcript Persistence

### Format

```json
{
  "session_id": "sess_abc123",
  "language": "en",
  "model": "fast",
  "duration_seconds": 45.6,
  "text": "Full transcript text concatenated...",
  "utterances": [
    {
      "id": 0,
      "start": 0.0,
      "end": 2.5,
      "text": "Hello world",
      "confidence": 0.95,
      "words": [
        {"word": "Hello", "start": 0.0, "end": 0.5, "confidence": 0.98},
        {"word": "world", "start": 0.6, "end": 1.0, "confidence": 0.92}
      ]
    }
  ],
  "metadata": {
    "created_at": "2025-01-28T12:01:00Z",
    "worker_id": "realtime-whisper-1",
    "encoding": "pcm_s16le",
    "sample_rate": 16000
  }
}
```

---

## Session Resume

### Soft Resume (Linking)

Sessions can be linked for continuity tracking:

```
WS /v1/audio/transcriptions/stream?resume_session_id=sess_abc123
```

**Behavior:**

- Creates new session with `previous_session_id` set
- Returns `resumed_from` in `session.begin` message
- Audio and transcripts are separate files
- Linked sessions can be merged in post-processing

**Response:**

```json
{
  "type": "session.begin",
  "session_id": "sess_def456",
  "resumed_from": "sess_abc123"
}
```

### Future: Hard Resume

True session resume (restore state, continue from checkpoint) requires:

- Periodic transcript checkpoints
- Audio position tracking
- State serialization

This is deferred to a future milestone.

---

## REST API Endpoints

### List Sessions

```
GET /v1/realtime/sessions
```

**Query Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `status` | string | Filter by status |
| `since` | datetime | Sessions started after |
| `until` | datetime | Sessions started before |
| `limit` | int | Max results (default 50) |
| `offset` | int | Pagination offset |

**Response:**

```json
{
  "sessions": [
    {
      "id": "sess_abc123",
      "status": "completed",
      "language": "en",
      "model": "fast",
      "audio_duration_seconds": 45.6,
      "utterance_count": 12,
      "word_count": 156,
      "started_at": "2025-01-28T12:00:00Z",
      "ended_at": "2025-01-28T12:01:00Z",
      "store_audio": true,
      "store_transcript": true
    }
  ],
  "total": 1,
  "limit": 50,
  "offset": 0
}
```

### Get Session Details

```
GET /v1/realtime/sessions/{session_id}
```

**Response:**

```json
{
  "id": "sess_abc123",
  "status": "completed",
  "language": "en",
  "model": "fast",
  "encoding": "pcm_s16le",
  "sample_rate": 16000,
  "audio_duration_seconds": 45.6,
  "utterance_count": 12,
  "word_count": 156,
  "store_audio": true,
  "store_transcript": true,
  "enhance_on_end": false,
  "audio_uri": "s3://bucket/sessions/sess_abc123/audio.wav",
  "transcript_uri": "s3://bucket/sessions/sess_abc123/transcript.json",
  "worker_id": "realtime-whisper-1",
  "client_ip": "192.168.1.100",
  "started_at": "2025-01-28T12:00:00Z",
  "ended_at": "2025-01-28T12:01:00Z"
}
```

### Download Audio

```
GET /v1/realtime/sessions/{session_id}/audio
```

Returns presigned S3 URL or streams audio directly.

### Download Transcript

```
GET /v1/realtime/sessions/{session_id}/transcript
```

Returns transcript JSON.

### Delete Session

```
DELETE /v1/realtime/sessions/{session_id}
```

Deletes a session and its associated data. Only non-active sessions can be deleted.

**Constraints:**

- Active sessions cannot be deleted (returns 409 Conflict)
- Only the tenant that owns the session can delete it

**Response:**

```json
{
  "deleted": true,
  "session_id": "sess_abc123"
}
```

**Error Responses:**

| Status | Description                   |
|--------|-------------------------------|
| 404    | Session not found             |
| 409    | Cannot delete active session  |

---

## Statistics Tracking

Stats are updated during the session to provide real-time visibility:

| Stat | Updated | Source |
|------|---------|--------|
| `audio_duration_seconds` | On each audio chunk | Calculated from bytes + sample rate |
| `utterance_count` | On each `transcript.final` | Incremented |
| `word_count` | On each `transcript.final` | Sum of words in utterance |

**Update frequency:** Every 10 seconds or on significant events.

---

## Web Console Integration

The web console displays realtime sessions alongside batch jobs:

### Sessions List View

- Status indicator (active/completed/error)
- Duration, utterance count
- Quick actions: view details, download audio/transcript

### Session Detail View

- Full metadata and parameters
- Timeline of utterances
- Audio player (if stored)
- Transcript viewer
- Link to enhancement job (if applicable)

### Real-time Updates

- Active sessions update via polling (every 5s)
- Status changes reflected immediately

---

## Related Documentation

- [Real-Time Architecture](./REALTIME.md) - Overall realtime design
- [Session Router](./SESSION_ROUTER.md) - Worker allocation
- [WebSocket API](./WEBSOCKET_API.md) - Protocol details
- [M24: Realtime Session Persistence](../../plan/milestones/M24-realtime-session-persistence.md) - Implementation milestone
