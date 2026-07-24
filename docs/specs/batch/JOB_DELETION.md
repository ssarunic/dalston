# Job deletion

Deletion permanently removes a terminal job record and its retained artifacts.
It is different from cancellation, which stops active work while preserving a
terminal `cancelled` record.

## API

```http
DELETE /v1/audio/transcriptions/{job_id}
Authorization: Bearer <key>
```

The console uses `DELETE /api/console/jobs/{job_id}`.

Successful deletion returns `204 No Content`. The endpoint returns `404` when
the job is absent or not visible to the caller, and `409` when the job is not
terminal.

| Status | Deletable |
| --- | --- |
| `pending` | No; cancel first |
| `running` | No; cancel first |
| `cancelling` | No; wait for terminal cancellation |
| `completed` | Yes |
| `failed` | Yes |
| `cancelled` | Yes |

## What is removed

The gateway authorizes the tenant-scoped job, deletes associated object/local
artifacts through the storage service, and removes the database record.
Database cascades remove task rows. Storage cleanup covers source audio,
intermediate task artifacts, final transcript/output, and redacted outputs
that belong to the job.

Deletion is best-effort across storage/database boundaries and is audited.
Operators should monitor storage failures and reconcile orphaned objects.

## Clients

The web console exposes deletion for terminal jobs with confirmation. The
current Python SDK and CLI do not expose job deletion; use the HTTP endpoint
when automation requires it.

Do not document a CLI job-delete subcommand or SDK delete method until those
surfaces exist and are tested.

## Retention

Scheduled retention purge and explicit deletion both remove artifacts, but
they have different triggers. Retention can leave a record that reports
`purged_at`; explicit deletion removes the job record. Backups and
storage-provider lifecycle rules remain an operator responsibility.
