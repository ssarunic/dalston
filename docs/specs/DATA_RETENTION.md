# Data retention

Batch jobs and persisted realtime sessions use one integer retention contract.

| Value | Meaning |
| --- | --- |
| omitted | Use the server default |
| `0` | Transient: do not retain artifacts after processing/session completion |
| `-1` | Permanent: never purge automatically |
| `1`–`3650` | Retain for that number of days |

The default is 30 days in distributed mode. Lite mode changes the default to
`0` unless `DALSTON_RETENTION_DEFAULT_DAYS` is explicitly set.

## API use

Batch:

```bash
curl -X POST http://localhost:8000/v1/audio/transcriptions \
  -H "Authorization: Bearer $DALSTON_API_KEY" \
  -F file=@audio.wav \
  -F retention=7
```

Native realtime:

```text
wss://HOST/v1/audio/transcriptions/stream?retention=7
```

The Python SDK accepts the same values through its batch and realtime request
parameters. The CLI exposes `--retention`.

API responses normalize retention to:

- `mode`: `none`, `keep`, or `auto_delete`;
- `hours` for time-limited retention;
- `purge_after` once the deadline is known;
- `purged_at` after cleanup.

## Stored data

Retention applies to durable artifacts associated with a job/session, including
source audio, transcript results, redacted outputs, PII metadata, and pipeline
intermediates where present. A record can outlive its artifacts so clients can
distinguish a purged result from a missing identifier.

Transient mode avoids durable artifact retention. Processing may still require
temporary working files or in-memory data. It is not a promise that bytes never
touch local temporary storage.

## Cleanup

The retention cleanup worker periodically selects expired terminal records,
deletes their stored artifacts, and records purge timestamps. Configure it
with:

- `DALSTON_RETENTION_CLEANUP_INTERVAL_SECONDS` (default `300`);
- `DALSTON_RETENTION_CLEANUP_BATCH_SIZE` (default `100`);
- `DALSTON_RETENTION_DEFAULT_DAYS`.

Permanent records have no purge deadline. Deleting a terminal job/session is an
explicit user operation and is separate from scheduled retention cleanup.

## Runtime-mode differences

Distributed mode uses PostgreSQL and object storage and defaults to 30 days.
Lite mode uses SQLite/local artifacts and defaults to transient. Some
historical artifact retrieval endpoints, especially realtime session
transcript/audio retrieval, return `409` in Lite mode even if metadata exists.

Do not use removed storage flags or a hybrid realtime mode to describe
retention. The integer request value, runtime default, and actual endpoint
capabilities are authoritative.

## Security and operations

Purging is not a substitute for storage-provider lifecycle policies, encrypted
backups, or audit-log retention. Operators must set compatible retention on
backups and replicas. A presigned download URL may remain valid for its short
expiry window after the underlying record changes.

Use audit events to record lifecycle actions; do not retain sensitive payloads
inside audit details merely to compensate for purged artifacts.
