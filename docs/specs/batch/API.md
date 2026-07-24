# Dalston HTTP API reference

This document is a human-readable map of the current public HTTP surface. The
running gateway's OpenAPI document (`/openapi.json`) is authoritative for
request schemas, response schemas, and validation constraints.

All `/v1` endpoints require `Authorization: Bearer <DALSTON_API_KEY>` unless
security is disabled for local development. API keys are managed through
`/auth/keys`; the current principal is available from `/auth/me`.

## Native batch transcription

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/v1/audio/transcriptions` | Submit audio or, in supported inline modes, transcribe immediately |
| `GET` | `/v1/audio/transcriptions` | List jobs |
| `GET` | `/v1/audio/transcriptions/{job_id}` | Get status and result |
| `PATCH` | `/v1/audio/transcriptions/{job_id}` | Rename a job |
| `POST` | `/v1/audio/transcriptions/{job_id}/cancel` | Request cancellation |
| `DELETE` | `/v1/audio/transcriptions/{job_id}` | Delete a terminal job and its stored artifacts |
| `GET` | `/v1/audio/transcriptions/{job_id}/audio` | Download original audio |
| `DELETE` | `/v1/audio/transcriptions/{job_id}/audio` | Delete stored original audio |
| `GET` | `/v1/audio/transcriptions/{job_id}/audio/redacted` | Download PII-redacted audio |
| `GET` | `/v1/audio/transcriptions/{job_id}/export/{format}` | Export the transcript |
| `GET` | `/v1/audio/transcriptions/{job_id}/tasks` | Inspect pipeline tasks |
| `GET` | `/v1/audio/transcriptions/{job_id}/tasks/{task_id}/artifacts` | Inspect task artifacts |

Submit exactly one of `file` or `audio_url` as multipart form data. Important
native fields are:

| Field | Meaning |
| --- | --- |
| `name` | Optional display name |
| `model` | Transcription model registry ID, engine alias, or `auto` |
| `model_diarize`, `model_align`, `model_pii_detect` | Optional stage-specific model IDs |
| `language` | Language code or `auto` |
| `vocabulary` | JSON array of at most 100 strings, each at most 50 characters |
| `speaker_detection` | `none`, `diarize`, or `per_channel` |
| `num_speakers`, `min_speakers`, `max_speakers` | Speaker-count constraints from 1 to 32 |
| `timestamps_granularity` | `none`, `segment`, or `word` |
| `pii_detection` | Run PII detection after transcription |
| `pii_entity_types` | Optional JSON array of entity types |
| `redact_pii_audio` | Produce a redacted audio artifact |
| `pii_redaction_mode` | `silence` or `beep` |
| `retention` | `0` transient, `-1` permanent, or 1–3650 days |
| `lite_profile` | Lite-only `core`, `speaker`, or `compliance` pipeline |

Native distributed submissions normally return `201` and a job ID. Lite mode
with transient retention may return the completed result inline. Poll
`GET /v1/audio/transcriptions/{job_id}` until `status` is `completed`,
`failed`, or `cancelled`.

Cancellation and deletion are different operations. Cancellation is valid for
pending or running work and transitions through `cancelling` when workers must
stop. Deletion is accepted only after the job reaches a terminal state.

### Input limits

- The probed audio duration may not exceed 10 hours.
- Native direct uploads do not have an application-level byte ceiling. Reverse
  proxies and infrastructure may impose their own limits.
- URL ingestion defaults to 3 GB and is controlled by
  `DALSTON_AUDIO_URL_MAX_SIZE_GB`.
- OpenAI-compatible uploads are limited to 25 MB.
- ElevenLabs-compatible uploads are limited to 3 GB.

## Results and exports

The job response includes input audio metadata, stages, retention information,
and either a result or an error. Result fields are capability-dependent:

- Language may be `und` when it cannot be determined.
- Language, segment, and word confidence values can be `null`.
- Requesting word timestamps does not guarantee them; engines that cannot
  produce them return segment-level data and a warning.
- Warnings can describe unavailable capabilities or low speech coverage.
- Original channel count, sample rate, codec, duration, and other preserved
  input metadata describe the uploaded audio, even when preparation creates a
  normalized working artifact.

Exports are available as `txt`, `json`, `srt`, and `vtt`. Subtitle exports
require timestamps in the result.

## Model and engine management

`/v1/models` is the persistent model registry. `/v1/engines` reports running
engine instances and capabilities.

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/v1/models` | List registry entries; filter by `stage`, `engine_id`, or `status` |
| `GET` | `/v1/models/{model_id}` | Get one registry entry |
| `PATCH` | `/v1/models/{model_id}` | Update editable metadata |
| `POST` | `/v1/models/{model_id}/pull` | Download model files |
| `DELETE` | `/v1/models/{model_id}` | Remove files; `purge=true` also removes the registry entry |
| `POST` | `/v1/models/sync` | Reconcile registry state with storage |
| `POST` | `/v1/models/hf/resolve` | Resolve a Hugging Face model to a compatible engine |
| `GET` | `/v1/models/hf/mappings` | List Hugging Face routing mappings |
| `GET` | `/v1/engines` | List live engines |
| `GET` | `/v1/engines/capabilities` | Summarize live engine capabilities |
| `GET` | `/v1/lite/capabilities` | Report Lite-mode feature availability |

There are no model load/unload or generic status convenience endpoints.

## ElevenLabs-compatible batch API

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/v1/speech-to-text` | Submit synchronous or asynchronous transcription |
| `GET` | `/v1/speech-to-text/transcripts/{transcription_id}` | Retrieve an asynchronous result |
| `DELETE` | `/v1/speech-to-text/transcripts/{transcription_id}` | Delete the corresponding terminal job |
| `GET` | `/v1/speech-to-text/transcripts/{transcription_id}/export/{format}` | Export the result |
| `POST` | `/v1/single-use-token/{token_type}` | Create a browser-safe realtime token |

See [ElevenLabs compatibility](../../guides/41-realtime-elevenlabs-compatible.md)
for realtime use and the exact adapter behavior.

## OpenAI-compatible API

`POST /v1/audio/transcriptions` switches to synchronous OpenAI-compatible
behavior when `model` is an OpenAI model name. `POST /v1/audio/translations`
provides translation. See [OpenAI-compatible API](../openai/API.md).

## Realtime management

The WebSocket protocols are documented in
[WebSocket API](../realtime/WEBSOCKET_API.md). Their management surface is:

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/v1/realtime/status` | Capacity summary |
| `GET` | `/v1/realtime/workers` | List workers |
| `GET` | `/v1/realtime/workers/{instance}` | Inspect one worker |
| `GET` | `/v1/realtime/sessions` | List persisted sessions |
| `GET` | `/v1/realtime/sessions/{session_id}` | Get session metadata |
| `DELETE` | `/v1/realtime/sessions/{session_id}` | Delete a non-active session |
| `GET` | `/v1/realtime/sessions/{session_id}/audio` | Download retained audio |
| `GET` | `/v1/realtime/sessions/{session_id}/transcript` | Download retained transcript JSON |
| `GET` | `/v1/realtime/sessions/{session_id}/export/{format}` | Export a retained transcript |
| `POST` | `/v1/realtime/transcription_sessions` | Create an OpenAI-compatible realtime session |

Transcript and audio retrieval require distributed mode.

## Webhooks and audit

Webhook endpoint management is available at `/v1/webhooks`. Individual
endpoints support read, update, delete, secret rotation, delivery listing, and
delivery retry. Delivery signatures use HMAC-SHA256 as documented in
[webhook verification](../examples/webhook-verification.md).

Audit queries are available from `GET /v1/audit` and
`GET /v1/audit/resources/{resource_type}/{resource_id}`.

## Rate limits and errors

Defaults are 600 requests/minute, 10 concurrent batch jobs, and 5 concurrent
realtime sessions per tenant. Configure them with:

- `DALSTON_RATE_LIMIT_REQUESTS_PER_MINUTE`
- `DALSTON_RATE_LIMIT_CONCURRENT_JOBS`
- `DALSTON_RATE_LIMIT_CONCURRENT_SESSIONS`

HTTP errors use FastAPI validation responses or Dalston's structured error
payloads. Compatibility routes translate errors into the target provider's
shape. Always use the status code and machine-readable error code rather than
matching human-readable text.
