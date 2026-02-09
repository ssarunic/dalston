# Dalston Data Model

## Overview

This document describes the data structures used in Dalston for jobs, tasks, and transcripts.

### Storage Architecture

| Layer | Technology | Purpose |
|-------|------------|---------|
| **PostgreSQL** | Primary database | Persistent business data (jobs, tasks, API keys, tenants) |
| **Redis** | In-memory store | Ephemeral data (session state, rate limits, queues, pub/sub) |
| **S3** | Object storage | All artifacts (audio files, transcripts, exports) |
| **Local** | Temp filesystem | In-flight processing files only |

---

## PostgreSQL Schemas

### Jobs Table

```sql
CREATE TABLE jobs (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID NOT NULL REFERENCES tenants(id),
    status          VARCHAR(20) NOT NULL DEFAULT 'pending',
    audio_uri       TEXT NOT NULL,
    parameters      JSONB NOT NULL DEFAULT '{}',
    webhook_url     TEXT,
    error           TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at      TIMESTAMPTZ,
    completed_at    TIMESTAMPTZ
);

CREATE INDEX idx_jobs_tenant_id ON jobs(tenant_id);
CREATE INDEX idx_jobs_status ON jobs(status);
CREATE INDEX idx_jobs_created_at ON jobs(created_at DESC);
```

| Column | Type | Description |
|--------|------|-------------|
| `id` | UUID | Unique job identifier |
| `tenant_id` | UUID | Tenant for multi-tenancy isolation |
| `status` | VARCHAR | pending, running, completed, failed, cancelled |
| `audio_uri` | TEXT | S3 URI to original audio file |
| `parameters` | JSONB | Job configuration |
| `webhook_url` | TEXT | Callback URL (optional) |
| `error` | TEXT | Error message if failed |
| `created_at` | TIMESTAMPTZ | When job was created |
| `started_at` | TIMESTAMPTZ | When processing began |
| `completed_at` | TIMESTAMPTZ | When processing finished |

#### Job Status Values

| Status | Description |
|--------|-------------|
| `pending` | Queued, waiting to start |
| `running` | Currently being processed |
| `completed` | Successfully finished |
| `failed` | Error occurred |
| `cancelled` | Cancelled by user |

---

### Tasks Table

```sql
CREATE TABLE tasks (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id          UUID NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    stage           VARCHAR(50) NOT NULL,
    engine_id       VARCHAR(100) NOT NULL,
    status          VARCHAR(20) NOT NULL DEFAULT 'pending',
    dependencies    UUID[] NOT NULL DEFAULT '{}',
    config          JSONB NOT NULL DEFAULT '{}',
    input_uri       TEXT,
    output_uri      TEXT,
    retries         INTEGER NOT NULL DEFAULT 0,
    max_retries     INTEGER NOT NULL DEFAULT 2,
    required        BOOLEAN NOT NULL DEFAULT true,
    error           TEXT,
    started_at      TIMESTAMPTZ,
    completed_at    TIMESTAMPTZ
);

CREATE INDEX idx_tasks_job_id ON tasks(job_id);
CREATE INDEX idx_tasks_status ON tasks(status);
CREATE INDEX idx_tasks_stage ON tasks(stage);
```

| Column | Type | Description |
|--------|------|-------------|
| `id` | UUID | Unique task identifier |
| `job_id` | UUID | Parent job ID |
| `stage` | VARCHAR | Pipeline stage (transcribe, align, diarize, etc.) |
| `engine_id` | VARCHAR | Engine to execute this task |
| `status` | VARCHAR | pending, ready, running, completed, failed, skipped |
| `dependencies` | UUID[] | Task IDs this task depends on |
| `config` | JSONB | Engine-specific configuration |
| `input_uri` | TEXT | S3 URI to input file |
| `output_uri` | TEXT | S3 URI to output file |
| `retries` | INTEGER | Current retry count |
| `max_retries` | INTEGER | Maximum retries allowed |
| `required` | BOOLEAN | If false, job continues on failure |
| `error` | TEXT | Error message if failed |
| `started_at` | TIMESTAMPTZ | When execution began |
| `completed_at` | TIMESTAMPTZ | When execution finished |

#### Task Status Values

| Status | Description |
|--------|-------------|
| `pending` | Waiting for dependencies |
| `ready` | Dependencies met, queued for execution |
| `running` | Currently being processed |
| `completed` | Successfully finished |
| `failed` | Error occurred |
| `skipped` | Skipped (optional task that failed) |

---

### Tenants Table

```sql
CREATE TABLE tenants (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name            VARCHAR(255) NOT NULL,
    settings        JSONB NOT NULL DEFAULT '{}',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX idx_tenants_name ON tenants(name);
```

| Column | Type | Description |
|--------|------|-------------|
| `id` | UUID | Unique tenant identifier |
| `name` | VARCHAR | Tenant name |
| `settings` | JSONB | Tenant-specific configuration |
| `created_at` | TIMESTAMPTZ | When tenant was created |
| `updated_at` | TIMESTAMPTZ | Last modification time |

---

### API Keys Table

```sql
CREATE TABLE api_keys (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    key_hash        VARCHAR(64) NOT NULL UNIQUE,
    prefix          VARCHAR(12) NOT NULL,
    name            VARCHAR(255) NOT NULL,
    tenant_id       UUID NOT NULL REFERENCES tenants(id),
    scopes          TEXT[] NOT NULL DEFAULT '{}',
    rate_limit      INTEGER,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_used_at    TIMESTAMPTZ,
    revoked_at      TIMESTAMPTZ
);

CREATE INDEX idx_api_keys_key_hash ON api_keys(key_hash);
CREATE INDEX idx_api_keys_tenant_id ON api_keys(tenant_id);
CREATE INDEX idx_api_keys_prefix ON api_keys(prefix);
```

| Column | Type | Description |
|--------|------|-------------|
| `id` | UUID | Unique key identifier |
| `key_hash` | VARCHAR | SHA256 hash of the full key |
| `prefix` | VARCHAR | First 10 chars for display (e.g., "dk_abc1234") |
| `name` | VARCHAR | Human-readable name |
| `tenant_id` | UUID | Tenant this key belongs to |
| `scopes` | TEXT[] | Permissions: jobs:read, jobs:write, realtime, webhooks, admin |
| `rate_limit` | INTEGER | Max requests/minute (null = use tenant default) |
| `created_at` | TIMESTAMPTZ | When key was created |
| `last_used_at` | TIMESTAMPTZ | When key was last used |
| `revoked_at` | TIMESTAMPTZ | When key was revoked (null if active) |

---

## Redis Data Structures

Redis is used exclusively for ephemeral, real-time data that doesn't require durability.

### Engine Work Queue

**Key**: `dalston:queue:{engine_id}`
**Type**: List (FIFO)

```
RPUSH dalston:queue:faster-whisper task_uuid
BRPOP dalston:queue:faster-whisper 30
```

Workers use `BRPOP` for blocking dequeue with timeout. Task UUIDs reference the PostgreSQL tasks table.

---

### Rate Limit Counter

**Key**: `dalston:ratelimit:{key_id}`
**Type**: String (counter)
**TTL**: 60 seconds

Incremented on each request, auto-expires for sliding window rate limiting.

---

### Real-time Session State

**Key**: `dalston:session:{session_id}`
**Type**: Hash
**TTL**: 300 seconds (extended on activity)

```json
{
  "session_id": "sess_abc123",
  "tenant_id": "uuid",
  "worker_id": "worker-1",
  "status": "active",
  "audio_duration_ms": 45000,
  "created_at": "2025-01-28T12:00:00Z",
  "last_activity": "2025-01-28T12:00:45Z"
}
```

Session state is ephemeral. Audio and transcripts are written to S3.

---

### Event Channel

**Channel**: `dalston:events`
**Type**: Pub/Sub

#### Event Types

**Job Created**

```json
{
  "type": "job.created",
  "job_id": "uuid",
  "timestamp": "2025-01-28T12:00:00Z"
}
```

**Task Completed**

```json
{
  "type": "task.completed",
  "task_id": "uuid",
  "job_id": "uuid",
  "stage": "transcribe",
  "timestamp": "2025-01-28T12:01:30Z"
}
```

**Task Failed**

```json
{
  "type": "task.failed",
  "task_id": "uuid",
  "job_id": "uuid",
  "error": "CUDA out of memory",
  "timestamp": "2025-01-28T12:01:15Z"
}
```

**Task Progress**

```json
{
  "type": "task.progress",
  "task_id": "uuid",
  "job_id": "uuid",
  "progress": 45,
  "timestamp": "2025-01-28T12:00:45Z"
}
```

**Job Completed**

```json
{
  "type": "job.completed",
  "job_id": "uuid",
  "timestamp": "2025-01-28T12:02:30Z"
}
```

---

## S3 Storage Structure

### Job Artifacts

```
s3://{bucket}/
├── jobs/
│   └── {job_id}/
│       ├── audio/
│       │   ├── original.mp3           # As uploaded
│       │   ├── original.wav           # Converted to WAV
│       │   ├── prepared.wav           # 16kHz, 16-bit (mono or stereo)
│       │   ├── channel_0.wav          # If split (per_channel mode)
│       │   └── channel_1.wav          # If split
│       │
│       ├── tasks/
│       │   └── {task_id}/
│       │       ├── input.json         # Task input specification
│       │       └── output.json        # Task output result
│       │
│       └── transcript.json            # Final merged result
│
├── sessions/
│   └── {session_id}/
│       ├── audio.wav                  # Recorded audio (if enabled)
│       ├── partials/                  # Streaming transcripts
│       │   ├── 0001.json
│       │   └── ...
│       └── final.json                 # Final session transcript
│
└── exports/
    └── {job_id}/
        ├── transcript.srt
        ├── transcript.vtt
        └── transcript.txt
```

### S3 URI Format

All `*_uri` columns in PostgreSQL use the format:

```
s3://{bucket}/{path}
```

Example:

```
s3://dalston-artifacts/jobs/550e8400-e29b-41d4-a716-446655440000/audio/original.wav
```

### Model Cache

Models are stored in S3 and cached locally on workers:

```
s3://{bucket}/models/
├── faster-whisper/
│   ├── large-v3/
│   ├── medium/
│   └── small/
├── pyannote/
│   └── speaker-diarization-3.1/
├── whisperx/
│   └── wav2vec2-large-xlsr/
└── emotion2vec/
    └── base/
```

Workers download models to local cache on startup:

```
/data/models/  (local cache, not persisted)
```

---

## Task Input/Output Format

### Task Input

**S3 Path**: `s3://{bucket}/jobs/{job_id}/tasks/{task_id}/input.json`

```json
{
  "task_id": "uuid",
  "job_id": "uuid",

  "audio_uri": "s3://bucket/jobs/{job_id}/audio/prepared.wav",

  "previous_outputs": {
    "prepare": {
      "duration": 150.5,
      "channels": 1,
      "sample_rate": 16000,
      "audio_uri": "s3://bucket/jobs/{job_id}/audio/prepared.wav"
    },
    "transcribe": {
      "text": "...",
      "segments": [...],
      "language": "en"
    }
  },

  "config": {
    "model": "large-v3",
    "language": "auto",
    "beam_size": 5,
    "vad_filter": true
  }
}
```

### Task Output

**S3 Path**: `s3://{bucket}/jobs/{job_id}/tasks/{task_id}/output.json`

```json
{
  "task_id": "uuid",
  "completed_at": "2025-01-28T12:01:30Z",
  "processing_time_seconds": 88.5,

  "data": {
    "text": "Welcome to the show. Thanks for having me...",
    "segments": [
      {
        "start": 0.0,
        "end": 3.5,
        "text": "Welcome to the show.",
        "words": [
          { "text": "Welcome", "start": 0.0, "end": 0.4, "confidence": 0.98 },
          { "text": "to", "start": 0.45, "end": 0.55, "confidence": 0.99 }
        ]
      }
    ],
    "language": "en",
    "language_confidence": 0.98
  }
}
```

---

## Final Transcript Format

**S3 Path**: `s3://{bucket}/jobs/{job_id}/transcript.json`

```json
{
  "job_id": "uuid",
  "version": "1.0",

  "metadata": {
    "audio_duration": 150.5,
    "audio_channels": 1,
    "language": "en",
    "created_at": "2025-01-28T12:00:00Z",
    "completed_at": "2025-01-28T12:02:30Z",
    "processing_time_seconds": 150,
    "pipeline_stages": ["prepare", "transcribe", "align", "diarize", "llm-cleanup", "merge"],
    "pipeline_warnings": []
  },

  "text": "Welcome to the show. Thanks for having me. Today we're going to talk about...",

  "speakers": [
    {
      "id": "SPEAKER_00",
      "label": "Sarah Chen",
      "channel": null
    },
    {
      "id": "SPEAKER_01",
      "label": "John Smith",
      "channel": null
    }
  ],

  "segments": [
    {
      "id": "seg_001",
      "start": 0.0,
      "end": 3.5,
      "text": "Welcome to the show.",
      "speaker": "SPEAKER_00",
      "words": [
        { "text": "Welcome", "start": 0.0, "end": 0.4, "confidence": 0.98 },
        { "text": "to", "start": 0.45, "end": 0.55, "confidence": 0.99 },
        { "text": "the", "start": 0.6, "end": 0.7, "confidence": 0.99 },
        { "text": "show", "start": 0.75, "end": 1.1, "confidence": 0.97 }
      ],
      "emotion": "positive",
      "emotion_confidence": 0.85,
      "events": []
    },
    {
      "id": "seg_002",
      "start": 3.5,
      "end": 5.2,
      "text": "Thanks for having me.",
      "speaker": "SPEAKER_01",
      "words": [...],
      "emotion": "positive",
      "emotion_confidence": 0.92,
      "events": []
    },
    {
      "id": "seg_003",
      "start": 5.2,
      "end": 7.5,
      "text": null,
      "speaker": null,
      "words": null,
      "emotion": null,
      "events": [
        {
          "type": "laughter",
          "start": 5.2,
          "end": 7.2,
          "confidence": 0.88
        }
      ]
    }
  ],

  "paragraphs": [
    {
      "id": "para_001",
      "start_segment": "seg_001",
      "end_segment": "seg_010",
      "topic": "Introduction"
    }
  ],

  "summary": "Sarah Chen interviews John Smith about his recent book on climate technology. Key topics include renewable energy investment, policy recommendations, and emerging technologies."
}
```

### Segment Fields

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Unique segment identifier |
| `start` | number | Start time in seconds |
| `end` | number | End time in seconds |
| `text` | string | Transcript text (null for non-speech) |
| `speaker` | string | Speaker ID (null for non-speech) |
| `words` | array | Word-level timestamps (if enabled) |
| `emotion` | string | Detected emotion (if enabled) |
| `emotion_confidence` | number | Emotion detection confidence |
| `events` | array | Audio events in this segment |

### Word Fields

| Field | Type | Description |
|-------|------|-------------|
| `text` | string | The word text |
| `start` | number | Start time in seconds |
| `end` | number | End time in seconds |
| `confidence` | number | Recognition confidence (0-1) |

### Audio Event Fields

| Field | Type | Description |
|-------|------|-------------|
| `type` | string | Event type (laughter, applause, music, silence) |
| `start` | number | Start time in seconds |
| `end` | number | End time in seconds |
| `confidence` | number | Detection confidence (0-1) |

### Speaker Fields

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Internal speaker ID (SPEAKER_00, SPEAKER_01) |
| `label` | string | Human-readable name (from LLM cleanup) |
| `channel` | number | Source channel (for per_channel mode) |

### Pipeline Warnings

When optional pipeline stages fail or are skipped, the `pipeline_warnings` array documents what happened. This allows clients to understand why certain features are missing from the output.

```json
{
  "metadata": {
    "pipeline_warnings": [
      {
        "stage": "diarize",
        "status": "skipped",
        "fallback": "single_speaker",
        "reason": "pyannote engine unavailable",
        "timestamp": "2025-01-28T12:01:30Z"
      },
      {
        "stage": "align",
        "status": "failed",
        "fallback": "transcription_timestamps",
        "reason": "whisperx-align failed after 2 retries",
        "timestamp": "2025-01-28T12:00:45Z"
      }
    ]
  }
}
```

| Field | Type | Description |
|-------|------|-------------|
| `stage` | string | Pipeline stage that had issues |
| `status` | string | `skipped` (not attempted) or `failed` (attempted but failed) |
| `fallback` | string | Fallback behavior applied (see table below) |
| `reason` | string | Human-readable explanation |
| `timestamp` | string | When the fallback was activated |

#### Fallback Values

| Stage | Fallback | Effect on Output |
|-------|----------|------------------|
| `align` | `transcription_timestamps` | Word timestamps from transcription engine, less precise |
| `diarize` | `single_speaker` | All segments assigned to `SPEAKER_00` |
| `detect_emotions` | `omitted` | No emotion fields in segments |
| `detect_events` | `omitted` | Empty events arrays |
| `refine` | `raw_transcription` | No speaker names, no paragraph/topic segmentation |

An empty `pipeline_warnings` array indicates all requested stages completed successfully.

---

## Local Temporary Storage

Engines use local storage only for in-flight processing. Files are downloaded from S3, processed, and results uploaded back to S3.

```
/tmp/dalston/
└── {task_id}/
    ├── input.wav      # Downloaded from S3
    ├── working/       # Intermediate files
    └── output.json    # Uploaded to S3, then deleted
```

Local files are cleaned up immediately after task completion or failure.
