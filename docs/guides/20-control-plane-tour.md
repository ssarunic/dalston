# Control plane tour — what's actually running

> The control plane is the always-on brain. It accepts API requests, stores
> jobs, schedules tasks across engines, and serves the web console. Engines
> are the muscle — they hold the GPU and run models. This page is the map.

---

## The picture

```
                    ┌─────────────────────────────────────────────────┐
                    │              CONTROL PLANE (always-on)          │
                    │                                                 │
   you ──HTTPS───►  │  ┌────────┐  ┌────────────┐  ┌────────────┐    │
   you ──WS─────►  │  │Gateway │  │Orchestrator│  │ Web console │    │
                    │  │ :8000  │  │            │  │ React (SPA) │    │
                    │  └───┬────┘  └─────┬──────┘  └─────────────┘    │
                    │      │             │                             │
                    │      ▼             ▼                             │
                    │  ┌────────┐  ┌────────────┐  ┌────────────┐    │
                    │  │Postgres│  │   Redis    │  │  S3/MinIO  │    │
                    │  │  jobs  │  │ events,    │  │ audio,     │    │
                    │  │ keys,  │  │ registry,  │  │ transcripts│    │
                    │  │ audit  │  │ rate-limits│  │ artifacts  │    │
                    │  └────────┘  └─────┬──────┘  └────────────┘    │
                    │                    │                             │
                    └────────────────────┼─────────────────────────────┘
                                         │ engine registry +
                                         │ task streams + heartbeats
                                         ▼
                    ┌─────────────────────────────────────────────────┐
                    │          ENGINE WORKERS (ephemeral)             │
                    │                                                 │
                    │   stt-prepare   stt-transcribe   stt-diarize    │
                    │   stt-align     stt-redact       stt-merge      │
                    │                                                 │
                    │   (CPU on control plane + GPU spot workers)     │
                    └─────────────────────────────────────────────────┘
```

In `make dev`, all of this lives on your laptop in one Docker network.
In split mode, the control plane is one EC2 box (t3.large on-demand) and the
GPU engines run on separate ephemeral spot boxes — the picture is the same,
just stretched across two machines and a Tailscale tunnel.

---

## The components

### Gateway (FastAPI, port 8000)

The HTTP / WebSocket front door. Handles auth, request validation, file
uploads, and routes work into Redis. Source: [`dalston/gateway/`](../../dalston/gateway/).

What it speaks:

| Path prefix | Compatibility | Source |
|---|---|---|
| `/v1/audio/transcriptions/...` | Dalston native + OpenAI (model-detected) | [`v1/transcription.py`](../../dalston/gateway/api/v1/transcription.py) |
| `/v1/speech-to-text/...` | ElevenLabs | [`v1/speech_to_text.py`](../../dalston/gateway/api/v1/speech_to_text.py) |
| `/v1/audio/translations` | OpenAI | [`v1/openai_translation.py`](../../dalston/gateway/api/v1/openai_translation.py) |
| `WS /v1/audio/transcriptions/stream` | Dalston native real-time | [`v1/realtime.py:145`](../../dalston/gateway/api/v1/realtime.py#L145) |
| `WS /v1/speech-to-text/realtime` | ElevenLabs real-time | [`v1/realtime.py:409`](../../dalston/gateway/api/v1/realtime.py#L409) |
| `WS /v1/realtime?intent=transcription` | OpenAI Realtime | [`v1/openai_realtime.py:612`](../../dalston/gateway/api/v1/openai_realtime.py#L612) |
| `/v1/jobs/stats`, `/v1/engines`, `/v1/realtime/sessions/*` | Operational | [`v1/router.py`](../../dalston/gateway/api/v1/router.py) |
| `/auth/keys`, `/auth/me`, `/auth/tokens` | Auth | [`api/auth.py`](../../dalston/gateway/api/auth.py) |

### Orchestrator

The DAG scheduler. Watches a durable Redis stream
(`dalston:events:stream`) for `job.created`, builds a per-job task graph
(`prepare → transcribe → align → diarize → merge`), and dispatches tasks to
matching engines. Re-queues stale tasks when workers die. Source:
[`dalston/orchestrator/`](../../dalston/orchestrator/).

Background workers it runs:

- `DeliveryWorker` — sends webhooks
- `CleanupWorker` — applies retention policy
- `StaleTaskScanner` — re-queues tasks from dead engines
- `ReconciliationSweeper` — keeps Postgres and Redis in agreement

### Postgres

Job records, API keys (SHA256-hashed), webhook configs, audit log,
realtime session ledger.

### Redis

Three roles:

1. **Engine registry** — `dalston:engine:instance:{id}` hash with 60s TTL.
   Engines heartbeat in; the gateway reads this for `/v1/engines`.
2. **Event stream** — `dalston:events:stream` (durable, consumer-group
   semantics). Job lifecycle events.
3. **Task queues** — one stream per engine instance. Tasks flow to whichever
   worker matches the capability and has capacity.
4. **Rate limits / session tokens** — `dalston:ratelimit:{key_id}` (60s
   window) and `tk_*` ephemeral tokens.

### S3 / MinIO

Audio uploads (`s3://bucket/jobs/{job_id}/audio.wav`), transcript outputs
(`transcript.json`), per-task artifacts (`tasks/{task_id}/output.json`).
Locally, MinIO serves the S3 API on port 9000.

### Web console (React SPA)

Built into the gateway image and served at `/`. Pages: Dashboard, Jobs,
Real-time, Engines, Models, Settings, Queue Board. See
[22-using-the-web-console.md](22-using-the-web-console.md).

---

## A request, end to end

Submitting a podcast for transcription:

1. **POST** `/v1/audio/transcriptions` with a multipart audio file. Gateway
   authenticates the API key against Postgres, then `StorageService.upload_audio`
   uploads to S3 at `jobs/{job_id}/audio.wav`. A `Job` row goes to Postgres
   with `status=pending`.
2. Gateway publishes `job.created` to `dalston:events:stream`. Returns the
   `Job` (HTTP 201, status=pending).
3. **Orchestrator** consumes `job.created`. Calls `dag.build_task_dag()` to
   construct `prepare → transcribe → [align] → [diarize] → merge` based on
   the request flags (`speaker_detection`, `timestamps_granularity`).
4. The orchestrator looks up engines that match each stage in the Redis
   registry, then `XADD`s a task to the chosen engine's input stream.
5. **Engine** picks up the task via `XREADGROUP`, downloads the audio
   artifact from S3, runs the model, writes the output JSON to S3 at
   `tasks/{task_id}/output.json`, and emits `task.completed`.
6. Orchestrator unblocks downstream tasks. Final stage is `merge`, which
   assembles `transcript.json` at `jobs/{job_id}/transcript.json` and emits
   `job.completed`.
7. **Webhook delivery worker** posts to any subscribed endpoints with a
   signed payload.
8. You poll `GET /v1/audio/transcriptions/{job_id}` (or wait for a webhook).
   Gateway hydrates the response from Postgres + S3.

---

## Talking to a control plane

Once a control plane is up, you reach it the same way regardless of where
it's running:

```bash
# Local
export DALSTON_SERVER=http://localhost:8000
export DALSTON_API_KEY=dk_...

# AWS split mode (over Tailscale)
export DALSTON_SERVER=https://dalston-control-plane.<your-tailnet>.ts.net
export DALSTON_API_KEY=dk_...
```

Then use any of:

```bash
dalston transcribe meeting.mp3
curl ... $DALSTON_SERVER/v1/audio/transcriptions ...
# or open the web console:
open $DALSTON_SERVER
```

---

## Engine ↔ Control plane wiring

Engines are not exposed to your laptop directly when the control plane is
running. The gateway proxies real-time WebSocket sessions to the chosen
worker (see `_proxy_to_worker` in [`v1/realtime.py:651`](../../dalston/gateway/api/v1/realtime.py#L651)),
and the orchestrator queues batch tasks via Redis streams.

For your client, **the gateway is the only address you talk to**. You can
add and remove engines at runtime — `dalston-aws launch gpu --engines
faster-whisper`, then later `--engines vllm-asr` — and the gateway picks
them up via the registry.

---

## See also

- [21-control-plane-aws-deploy.md](21-control-plane-aws-deploy.md) — actually deploy the control plane to AWS
- [22-using-the-web-console.md](22-using-the-web-console.md) — the GUI
- [23-using-the-cli.md](23-using-the-cli.md) — `dalston` CLI
- [24-using-the-python-sdk.md](24-using-the-python-sdk.md) — Python SDK
- [`docs/specs/ARCHITECTURE.md`](../specs/ARCHITECTURE.md) — engineering deep-dive
- [`docs/specs/batch/ORCHESTRATOR.md`](../specs/batch/ORCHESTRATOR.md) — orchestrator internals
