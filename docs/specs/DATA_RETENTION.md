# Data Retention

## Overview

Dalston uses a simple integer-based retention model for all audio and transcript artifacts. Retention is specified per-job (batch) or per-session (realtime) as a single integer parameter.

## Retention Values

| Value | Meaning | Behavior |
|-------|---------|----------|
| `0` | Transient | No storage - artifacts deleted immediately after processing |
| `-1` | Permanent | Keep forever - never auto-delete |
| `1-3650` | Days | Delete after N days from job completion / session end |

**Default:** 30 days

## API Usage

### Batch Jobs

```bash
# 30-day retention (default)
curl -X POST http://localhost:8000/v1/audio/transcriptions \
  -F "file=@meeting.mp3"

# Transient - no storage
curl -X POST http://localhost:8000/v1/audio/transcriptions \
  -F "file=@meeting.mp3" \
  -F "retention=0"

# Permanent - keep forever
curl -X POST http://localhost:8000/v1/audio/transcriptions \
  -F "file=@meeting.mp3" \
  -F "retention=-1"

# 90-day retention
curl -X POST http://localhost:8000/v1/audio/transcriptions \
  -F "file=@meeting.mp3" \
  -F "retention=90"
```

### Realtime Sessions

```
# 30-day retention (default)
ws://localhost:8000/v1/audio/transcriptions/stream

# Transient - no storage
ws://localhost:8000/v1/audio/transcriptions/stream?retention=0

# 7-day retention
ws://localhost:8000/v1/audio/transcriptions/stream?retention=7
```

### SDK

```python
from dalston_sdk import DalstonClient

client = DalstonClient(api_key="dk_xxx")

# Batch with 90-day retention
job = client.transcribe("meeting.mp3", retention=90)

# Transient (no storage)
job = client.transcribe("meeting.mp3", retention=0)
```

### CLI

```bash
# 90-day retention
dalston transcribe meeting.mp3 --retention 90

# Transient
dalston transcribe meeting.mp3 --retention 0

# Permanent
dalston transcribe meeting.mp3 --retention -1
```

## Data Model

### Jobs Table

| Column | Type | Description |
|--------|------|-------------|
| `retention` | INTEGER | Retention days (0=transient, -1=permanent, N=days) |
| `purge_after` | TIMESTAMPTZ | Computed: `completed_at + retention days` |
| `purged_at` | TIMESTAMPTZ | When artifacts were deleted |

### Realtime Sessions Table

| Column | Type | Description |
|--------|------|-------------|
| `retention` | INTEGER | Retention days (0=transient, -1=permanent, N=days) |
| `purge_after` | TIMESTAMPTZ | Computed: `ended_at + retention days` |
| `purged_at` | TIMESTAMPTZ | When artifacts were deleted |

## Cleanup Worker

A background worker periodically purges expired jobs and sessions:

```
Every 5 minutes:
  1. Query jobs/sessions where:
     - purge_after IS NOT NULL
     - purge_after <= NOW()
     - purged_at IS NULL

  2. For each expired item:
     - Delete S3 artifacts (audio, transcript, intermediates)
     - Set purged_at = NOW()
     - Emit audit event
```

### Transient Mode

For `retention=0`, artifacts are deleted inline when the job completes or session ends - no waiting for the cleanup worker.

## API Response

Job and session responses include retention information:

```json
{
  "id": "job_abc123",
  "status": "completed",
  "retention": {
    "mode": "auto_delete",
    "hours": 720,
    "purge_after": "2026-03-26T12:00:00Z",
    "purged_at": null
  }
}
```

After purge:

```json
{
  "retention": {
    "mode": "auto_delete",
    "hours": 720,
    "purge_after": "2026-03-26T12:00:00Z",
    "purged_at": "2026-03-26T12:05:00Z"
  }
}
```

## Web Console Display

The retention card shows:

- **Main text:** Original retention period (e.g., "30 days", "Permanent", "Transient")
- **Subtitle:** Countdown until purge (e.g., "5d 3h until purge") or status ("Purged", "No storage")

## Accessing Purged Artifacts

Attempting to download audio or transcript for a purged job returns:

```json
{
  "error": {
    "code": "artifacts_purged",
    "message": "Job artifacts were purged at 2026-03-26T12:05:00Z"
  }
}
```

HTTP status: `410 Gone`

## Configuration

| Environment Variable | Default | Description |
| -------------------- | ------- | ----------- |
| `RETENTION_DEFAULT_DAYS` | `30` | Default retention for new jobs/sessions |
| `RETENTION_MAX_DAYS` | `3650` | Maximum allowed retention (10 years) |
| `RETENTION_CLEANUP_INTERVAL_SECONDS` | `300` | Cleanup worker interval |
| `RETENTION_CLEANUP_BATCH_SIZE` | `100` | Jobs processed per cleanup cycle |

## Related Documentation

- [Audit Log](AUDIT_LOG.md) - Retention events are logged
- [Session Persistence](realtime/SESSION_PERSISTENCE.md) - Realtime storage
