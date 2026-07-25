# Job cancellation

Cancellation stops work while preserving the job record and its final status.
It is distinct from deletion, which removes a terminal job and stored
artifacts.

## API

```http
POST /v1/audio/transcriptions/{job_id}/cancel
Authorization: Bearer <key>
```

The equivalent console route is
`POST /api/console/jobs/{job_id}/cancel`. The Python SDK exposes
`client.cancel(job_id)`, and the CLI exposes `dalston jobs cancel JOB_ID`.

The endpoint returns:

- `200` when cancellation has been accepted, including when a pending job can
  be cancelled immediately.
- `404` when the job is absent or not visible to the caller.
- `409` when the job is already terminal or otherwise not cancellable.

## State transitions

```text
pending -> cancelled
running -> cancelling -> cancelled
```

`pending` jobs have no active engine work and can transition directly.
`running` jobs first become `cancelling`; the orchestrator publishes task
cancellation, prevents new dependent tasks from being queued, and finalizes the
job after active work acknowledges or is reconciled.

Terminal states are `completed`, `failed`, and `cancelled`. Repeating a
cancellation request for a terminal job returns `409`; callers should treat
the current job status as authoritative.

## Worker and orchestrator contract

- Engines observe cancellation through the task execution/cancellation
  contract and must stop without publishing a successful completion.
- Completion events racing with cancellation do not reopen the DAG or schedule
  downstream work.
- The orchestrator owns the final job transition and emits
  `transcription.cancelled`.
- Concurrent-job accounting is released exactly once.

The cancellation request is best-effort for external work that cannot be
interrupted immediately. A job can remain in `cancelling` briefly while its
lease or worker state is reconciled.

## Retention and deletion

Cancellation does not itself delete the job. Any retained partial artifacts
remain subject to the job's retention setting. Once cancellation is terminal,
`DELETE /v1/audio/transcriptions/{job_id}` can remove the job and its stored
artifacts.

## Client guidance

After receiving `200`, continue polling until the status is `cancelled`.
Webhook users can subscribe to `transcription.cancelled`. UIs should display
`cancelling` as an in-progress terminalization state and should not offer
delete until the job is terminal.

## Verification

The executable behavior is covered by unit/integration cancellation tests and
`tests/e2e/test_job_cancellation_e2e.py`. When changing this contract, update
the OpenAPI operation, SDK/CLI behavior, webhook event, and state-machine tests
together.
