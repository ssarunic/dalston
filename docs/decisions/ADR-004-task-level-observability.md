# ADR-004: Two-Tier Task Observability (Stage Breakdown + Artifact Inspection)

## Status

Accepted

## Context

Dalston's batch pipeline expands each job into a DAG of tasks (see [ADR-003](ADR-003-two-level-queues.md)). The orchestrator tracks each task's status, timing, and errors in PostgreSQL, and every task's input/output is persisted in S3. However, none of this information is exposed through the API.

The current job status response returns only `status`, `progress`, and `current_stage` — a flat view that treats the multi-stage pipeline as a black box. When a job fails, produces unexpected output, or runs slowly, neither the API consumer nor the operator can determine which stage caused the problem without access to the database and S3 directly.

We need to decide how to expose task-level information through the API, and in particular, how much detail to give to different audiences:

1. Regular API consumers who want to understand job failures and performance
2. Operators and power users who need to inspect intermediate data for debugging

## Options Considered

### 1. Expose Everything to All Users

Add the full `stages` array with embedded input/output artifacts directly on the job status response.

**Pros:**

- Simple — one endpoint, one response shape
- No new authorization decisions

**Cons:**

- Response size becomes unpredictable (artifact data can be large — transcription output for a 2-hour file contains thousands of segments)
- Mixes summary data with detail data in the same response
- API consumers who poll for status get far more data than they need
- No way to opt out of the overhead

### 2. Two-Tier: Summary on Job Status + Separate Artifact Endpoint (Chosen)

Embed a lightweight `stages` array (status, timing, errors) on the job status response. Provide a separate endpoint for fetching full input/output artifacts per task.

**Pros:**

- Stage summary is small and always useful — adds minimal overhead to status polling
- Artifact inspection is opt-in, only fetched when needed
- Clean separation between "what happened" (summary) and "what was the data" (artifacts)
- Artifact endpoint can evolve independently (e.g., add streaming, pagination for large outputs)

**Cons:**

- Two endpoints instead of one
- Requires task_id from the summary to call the artifact endpoint (two-step workflow)

### 3. Admin-Only Observability

Keep the job status response unchanged. Add an admin-scoped endpoint for task and artifact inspection.

**Pros:**

- No changes to the existing API contract
- Artifacts only accessible to operators

**Cons:**

- Regular users still cannot diagnose their own failures
- Creates support burden — users must ask operators to look up task data for them
- The data belongs to the user's job; restricting access to it is arbitrary
- Users building on the API cannot self-serve when debugging integration issues

## Decision

Implement **Option 2: Two-Tier Observability**.

### Stage Breakdown (Tier 1 — Embedded in Job Status)

The `GET /v1/audio/transcriptions/{id}` response includes a `stages` array whenever the job has been expanded into tasks. Each entry contains:

- `stage`, `task_id`, `engine_id`, `status`
- `started_at`, `completed_at`, `duration_ms`
- `required`, `retries`, `error`

This is a summary — no input/output data, no large payloads. It answers: *what ran, in what order, how long did it take, and what failed?*

### Artifact Inspection (Tier 2 — Separate Endpoint)

A new endpoint `GET /v1/audio/transcriptions/{job_id}/tasks/{task_id}/artifacts` returns the full input and output JSON for a specific task, fetched from S3.

This is available to any authenticated user with `jobs:read` scope for their own jobs — not restricted to admins. The rationale: the data is derived from the user's own audio and processing parameters. There is no security reason to withhold it.

### ElevenLabs Compatibility

The `stages` array and task endpoints are Dalston-native extensions. The ElevenLabs-compatible endpoints (`/v1/speech-to-text/*`) are not modified.

## Consequences

### Easier

- **Debugging job failures**: Users see exactly which stage failed, with the error message, without contacting support
- **Performance analysis**: Stage durations reveal bottlenecks (e.g., diarization taking 80% of total time)
- **Quality investigation**: Comparing transcription output before and after alignment shows where timestamps were adjusted
- **Pipeline understanding**: New users can see how their parameters translate into pipeline stages
- **Console UI**: The web console can render a pipeline timeline with drill-down, backed by these endpoints

### Harder

- **Response schema evolution**: The `stages` array is a new contract that must be maintained. Adding or renaming pipeline stages affects this response.
- **S3 access from gateway**: The artifact endpoint requires the gateway to fetch from S3, which it already does for transcripts but now does for intermediate task data too.
- **Testing**: Job status tests must now validate the `stages` array shape and ordering.

### Mitigations

- The `stages` array schema is derived from the existing `tasks` table — no new storage, minimal mapping code
- S3 fetches for artifacts are on-demand (user explicitly requests them), not on every status poll
- Stage ordering uses the existing DAG dependency graph — no new sorting logic needed
