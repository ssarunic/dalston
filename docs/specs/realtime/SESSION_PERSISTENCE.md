# Realtime session persistence

Realtime persistence is retention-driven. The shared gateway proxy can create a
PostgreSQL session record, collect final session statistics, and store audio and
transcript artifacts through the configured storage backend.

It is not a hybrid batch workflow. Ending a realtime session does not create an
enhancement job.

## Protocol coverage

- Dalston-native sessions pass persistence parameters to the shared proxy.
- OpenAI-compatible transcription sessions also create persisted session
  records.
- ElevenLabs-compatible realtime currently uses Redis-only coordination and
  does not promise a record in the session-history API.

## Retention

The `retention` value uses the same integer contract as batch jobs:

| Value | Meaning |
| --- | --- |
| omitted | Server default |
| `0` | Transient; do not retain artifacts |
| `-1` | Permanent |
| `1`–`3650` | Days until automatic purge |

The distributed default is 30 days. Lite mode changes the default to transient
unless explicitly overridden. Storage availability also depends on runtime
mode; realtime transcript and audio retrieval return `409` in Lite mode.

## Session metadata

Persisted records include status, language, requested model, resolved engine,
encoding, sample rate, audio duration, segment and word counts, retention and
purge timestamps, artifact URIs, worker instance, client IP, start/end time,
error, and optional `previous_session_id`.

Statuses exposed by the service include active and terminal outcomes such as
`completed`, `error`, and `interrupted`.

## Resume lineage

Dalston-native clients can provide `resume_session_id`. Dalston validates it as
a session UUID and stores it as `previous_session_id` on the new record.

This is soft resume:

- it records lineage;
- it does not restore worker decoder/VAD state;
- it does not append to or merge the previous transcript;
- it does not reuse the previous session ID.

Applications must combine linked results themselves if they need one logical
conversation.

## Management API

| Method | Path | Notes |
| --- | --- | --- |
| `GET` | `/v1/realtime/sessions` | Cursor pagination; filters include status and time range |
| `GET` | `/v1/realtime/sessions/{session_id}` | Metadata and artifact availability |
| `DELETE` | `/v1/realtime/sessions/{session_id}` | Terminal sessions only |
| `GET` | `/v1/realtime/sessions/{session_id}/transcript` | Stored transcript JSON; distributed mode |
| `GET` | `/v1/realtime/sessions/{session_id}/audio` | Presigned audio download; distributed mode |
| `GET` | `/v1/realtime/sessions/{session_id}/export/{format}` | `txt`, `json`, `srt`, or `vtt` |

List pagination defaults to 50 and accepts 1–100. Sort values are
`started_desc` and `started_asc`. Subtitle export accepts line-length and
line-count options.

Deletion rejects active sessions with `409`. Missing, purged, transient, or
unstored artifacts return the endpoint-specific not-found/conflict response;
clients should not assume every history row has downloadable files.

## Stored transcript shape

The storage artifact is internal session transcript JSON. The retrieval
endpoint normalizes it to a text value plus utterances with ID, start, end, and
text. Export formatting uses those utterances. Provider-protocol event streams
are not stored verbatim.

## Security

Every operation is tenant-scoped through the authenticated principal. Returned
audio URLs are short-lived presigned URLs. Deleting a session removes its
record and associated retained artifacts through the session service; audit
events should be used for compliance history, not the deleted record.
