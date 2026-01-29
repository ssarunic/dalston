# Dalston Data Model

## Overview

This document describes the data structures used in Dalston for jobs, tasks, and transcripts.

---

## Redis Data Structures

### Job State

**Key**: `dalston:job:{job_id}`  
**Type**: Hash

```json
{
  "id": "job_abc123",
  "tenant_id": "default",
  "status": "running",
  "audio_path": "/data/jobs/job_abc123/audio/original.wav",
  "parameters": "{\"speaker_detection\": \"diarize\", \"word_timestamps\": true}",
  "created_at": "2025-01-28T12:00:00Z",
  "started_at": "2025-01-28T12:00:01Z",
  "completed_at": null,
  "webhook_url": "https://example.com/webhook",
  "error": null
}
```

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Unique job identifier |
| `tenant_id` | string | Tenant ID for multi-tenancy isolation |
| `status` | string | pending, running, completed, failed, cancelled |
| `audio_path` | string | Path to original audio file |
| `parameters` | JSON string | Job configuration |
| `created_at` | ISO timestamp | When job was created |
| `started_at` | ISO timestamp | When processing began |
| `completed_at` | ISO timestamp | When processing finished |
| `webhook_url` | string | Callback URL (optional) |
| `error` | string | Error message if failed |

### Jobs by Tenant Index

**Key**: `dalston:jobs:tenant:{tenant_id}`
**Type**: Set

Contains all job IDs belonging to a tenant. Used for listing jobs scoped to an API key.

```
SMEMBERS dalston:jobs:tenant:default
→ ["job_abc123", "job_def456", ...]
```

---

### API Key State

**Key**: `dalston:apikeys:{key_hash}`
**Type**: String (JSON)

API keys are stored by their SHA256 hash for secure O(1) lookup.

```json
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "key_hash": "a1b2c3d4...",
  "prefix": "dk_abc1234",
  "name": "Production Key",
  "tenant_id": "default",
  "scopes": ["jobs:read", "jobs:write", "realtime"],
  "rate_limit": 100,
  "created_at": "2025-01-28T12:00:00Z",
  "last_used_at": "2025-01-28T14:30:00Z",
  "revoked_at": null
}
```

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Unique key identifier (UUID) |
| `key_hash` | string | SHA256 hash of the full key |
| `prefix` | string | First 10 chars for display (e.g., "dk_abc1234") |
| `name` | string | Human-readable name |
| `tenant_id` | string | Tenant this key belongs to |
| `scopes` | array | Permissions: jobs:read, jobs:write, realtime, webhooks, admin |
| `rate_limit` | integer | Max requests/minute (null = unlimited) |
| `created_at` | ISO timestamp | When key was created |
| `last_used_at` | ISO timestamp | When key was last used |
| `revoked_at` | ISO timestamp | When key was revoked (null if active) |

### API Key Indexes

**Key**: `dalston:apikeys:id:{key_id}` → `key_hash`
Lookup key hash by ID for management operations.

**Key**: `dalston:apikeys:tenant:{tenant_id}`
**Type**: Set
All key IDs belonging to a tenant.

### Rate Limit Counter

**Key**: `dalston:ratelimit:{key_id}`
**Type**: String (counter)
**TTL**: 60 seconds

Incremented on each request, auto-expires for sliding window rate limiting.

---

### Task State

**Key**: `dalston:task:{task_id}`  
**Type**: Hash

```json
{
  "id": "task_xyz789",
  "job_id": "job_abc123",
  "stage": "transcribe",
  "engine_id": "faster-whisper",
  "status": "completed",
  "dependencies": "[\"task_xyz788\"]",
  "config": "{\"language\": \"auto\", \"model\": \"large-v3\"}",
  "input_path": "/data/jobs/job_abc123/tasks/task_xyz789/input.json",
  "output_path": "/data/jobs/job_abc123/tasks/task_xyz789/output.json",
  "retries": 0,
  "max_retries": 2,
  "required": true,
  "started_at": "2025-01-28T12:00:02Z",
  "completed_at": "2025-01-28T12:01:30Z",
  "error": null
}
```

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Unique task identifier |
| `job_id` | string | Parent job ID |
| `stage` | string | Pipeline stage (transcribe, align, diarize, etc.) |
| `engine_id` | string | Engine to execute this task |
| `status` | string | pending, ready, running, completed, failed, skipped |
| `dependencies` | JSON array | Task IDs this task depends on |
| `config` | JSON string | Engine-specific configuration |
| `input_path` | string | Path to input file |
| `output_path` | string | Path to output file |
| `retries` | integer | Current retry count |
| `max_retries` | integer | Maximum retries allowed |
| `required` | boolean | If false, job continues on failure |
| `started_at` | ISO timestamp | When execution began |
| `completed_at` | ISO timestamp | When execution finished |
| `error` | string | Error message if failed |

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

### Job Tasks Index

**Key**: `dalston:job:{job_id}:tasks`  
**Type**: Set

Contains all task IDs belonging to a job.

```
SMEMBERS dalston:job:job_abc123:tasks
→ ["task_xyz788", "task_xyz789", "task_xyz790", ...]
```

---

### Engine Work Queue

**Key**: `dalston:queue:{engine_id}`  
**Type**: List (FIFO)

```
RPUSH dalston:queue:faster-whisper task_xyz789
BRPOP dalston:queue:faster-whisper 30
```

Workers use `BRPOP` for blocking dequeue with timeout.

---

### Recent Jobs Index

**Key**: `dalston:jobs:recent`  
**Type**: List

Most recent job IDs (capped to last 1000).

```
LPUSH dalston:jobs:recent job_abc123
LTRIM dalston:jobs:recent 0 999
```

---

### Event Channel

**Channel**: `dalston:events`  
**Type**: Pub/Sub

#### Event Types

**Job Created**
```json
{
  "type": "job.created",
  "job_id": "job_abc123",
  "timestamp": "2025-01-28T12:00:00Z"
}
```

**Task Completed**
```json
{
  "type": "task.completed",
  "task_id": "task_xyz789",
  "job_id": "job_abc123",
  "stage": "transcribe",
  "timestamp": "2025-01-28T12:01:30Z"
}
```

**Task Failed**
```json
{
  "type": "task.failed",
  "task_id": "task_xyz789",
  "job_id": "job_abc123",
  "error": "CUDA out of memory",
  "timestamp": "2025-01-28T12:01:15Z"
}
```

**Task Progress**
```json
{
  "type": "task.progress",
  "task_id": "task_xyz789",
  "job_id": "job_abc123",
  "progress": 45,
  "timestamp": "2025-01-28T12:00:45Z"
}
```

**Job Completed**
```json
{
  "type": "job.completed",
  "job_id": "job_abc123",
  "timestamp": "2025-01-28T12:02:30Z"
}
```

---

## Filesystem Structure

### Job Workspace

```
/data/jobs/{job_id}/
├── audio/
│   ├── original.mp3           # As uploaded
│   ├── original.wav           # Converted to WAV
│   ├── prepared.wav           # 16kHz, 16-bit (mono or stereo)
│   ├── channel_0.wav          # If split (per_channel mode)
│   └── channel_1.wav          # If split
│
├── tasks/
│   ├── {task_id}/
│   │   ├── input.json         # Task input specification
│   │   └── output.json        # Task output result
│   └── ...
│
└── transcript.json            # Final merged result
```

### Model Cache

```
/data/models/
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

---

## Task Input/Output Format

### Task Input

**Path**: `/data/jobs/{job_id}/tasks/{task_id}/input.json`

```json
{
  "task_id": "task_xyz789",
  "job_id": "job_abc123",
  
  "audio_path": "/data/jobs/job_abc123/audio/prepared.wav",
  
  "previous_outputs": {
    "prepare": {
      "duration": 150.5,
      "channels": 1,
      "sample_rate": 16000,
      "audio_path": "/data/jobs/job_abc123/audio/prepared.wav"
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

**Path**: `/data/jobs/{job_id}/tasks/{task_id}/output.json`

```json
{
  "task_id": "task_xyz789",
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
          { "word": "Welcome", "start": 0.0, "end": 0.4, "confidence": 0.98 },
          { "word": "to", "start": 0.45, "end": 0.55, "confidence": 0.99 }
        ]
      }
    ],
    "language": "en",
    "language_probability": 0.98
  }
}
```

---

## Final Transcript Format

**Path**: `/data/jobs/{job_id}/transcript.json`

```json
{
  "job_id": "job_abc123",
  "version": "1.0",
  
  "metadata": {
    "audio_duration": 150.5,
    "audio_channels": 1,
    "language": "en",
    "created_at": "2025-01-28T12:00:00Z",
    "completed_at": "2025-01-28T12:02:30Z",
    "processing_time_seconds": 150,
    "pipeline_stages": ["prepare", "transcribe", "align", "diarize", "llm-cleanup", "merge"]
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
        { "word": "Welcome", "start": 0.0, "end": 0.4, "confidence": 0.98 },
        { "word": "to", "start": 0.45, "end": 0.55, "confidence": 0.99 },
        { "word": "the", "start": 0.6, "end": 0.7, "confidence": 0.99 },
        { "word": "show", "start": 0.75, "end": 1.1, "confidence": 0.97 }
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
| `word` | string | The word |
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
