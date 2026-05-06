# Using the web console

> One URL, every observability surface you need: live transcription in the
> browser, job dashboard, engine health, queue board, model catalog, API key
> management, audit log. Built into the gateway image — no separate deploy.

The console is a React SPA served by the gateway at `/`. The same URL hosts
the API, so wherever you reach the API you reach the console.

| Where you're running | URL |
|---|---|
| `make dev` | <http://localhost:8000/> |
| `dalston-aws` (split mode, over Tailscale) | <https://dalston-control-plane.\><your-tailnet\>.ts.net/ |

Login uses your API key. Once authenticated, the SPA stores a session token
and refreshes it as needed.

---

## The pages

Source of truth: [`web/src/App.tsx`](../../web/src/App.tsx).

### Dashboard — `/`

At-a-glance: jobs running / queued / completed today / failed today, an
activity feed of recent jobs, and a row of engine status pills.

### Queue Board — `/queue`

Live view of the orchestrator's task queues. One column per stage
(`prepare`, `transcribe`, `align`, `diarize`, `merge`). Tasks move
left-to-right as they complete. Click a task to drill into its logs.

Use this when something is stuck — instantly see which stage is blocked.

### Batch Jobs — `/jobs`

Paginated list with filters (status, model, date range). Click into a job
for full detail:

- **`/jobs/:jobId`** — JobDetail: input audio info, current status,
  timeline, transcript preview, exports (txt / json / srt / vtt),
  PII summary, retention countdown.
- **`/jobs/:jobId/tasks/:taskId`** — TaskDetail: per-task input/output
  artifacts, logs, retry button, timing breakdown.

### New Job — `/jobs/new`

The "transcribe a file" form. Drop an audio file, pick a model (or `auto`),
toggle speaker detection, set timestamp granularity, choose retention. Then
watch it stream into the queue board.

Useful for trying engine combos without writing code.

### Real-time Sessions — `/realtime`

List of active and recent WebSocket sessions. Per-session detail at
`/realtime/sessions/:sessionId` shows duration, language, model, transcript
captured so far, VAD events.

### Real-time Live — `/realtime/live`

**In-browser microphone capture.** This is the demo page — click "start," let
the browser ask for mic permission, and watch transcripts stream as you
speak. Uses the Dalston native WebSocket protocol with binary PCM frames.

> Mic capture requires a secure context. Works on `localhost` and on
> `https://*.ts.net` (real Let's Encrypt cert via Tailscale serve). Will not
> work on a plain `http://` non-localhost URL.

### Engines — `/engines`

Every engine the gateway knows about, with status, capacity, models loaded,
RTF, and which stage(s) it serves. Filter by stage, mode (batch / realtime),
or status.

### Engine Detail — `/engines/:engineId`

Per-engine timing, recent tasks processed, model load history,
configuration parameters, link to docker container.

### Infrastructure — `/infrastructure`

The big-picture view: control plane services, Redis stream depth, Postgres
status, S3 connectivity, GPU worker presence. The kind of page you bookmark
when you're operating it.

### Models — `/models`

Discoverable model catalog. Each engine reports the models it can load; this
page aggregates them into one searchable list.

### API Keys — `/keys`

Mint, view, and revoke API keys. Shows the prefix (first 10 chars) and never
the full secret again after creation. Set scopes (`jobs:read`, `jobs:write`,
`realtime`, `webhooks`, `admin`).

### Webhooks — `/webhooks`, `/webhooks/:endpointId`

Configure webhook endpoints. Pick events (`job.completed`, `job.failed`,
etc.), see delivery history, view signature secrets, replay failed
deliveries.

### Audit Log — `/audit`

Every API key creation, deletion, job submission, configuration change. Used
for compliance and debugging "who did what."

### Settings — `/settings`

App-level settings (default retention, log format, etc).

---

## When to use the console vs the CLI/SDK

The console wins for:

- **Live debugging** — Queue board + job detail beats `dalston jobs get` when
  something is stuck
- **In-browser mic demos** — `/realtime/live` is the fastest "wow" moment
- **Onboarding teammates** — they don't need API keys to look around (after
  you give them one)
- **API key management** — minting and revoking keys is a UI workflow, not
  a script
- **Model discovery** — exploring what's loaded and available

The CLI / SDK wins for:

- Scripted batch submission
- CI / CD pipelines
- Programmatic transcript export
- Automation around webhooks

The two do not conflict — both speak the same API.

---

## See also

- [21-control-plane-aws-deploy.md](21-control-plane-aws-deploy.md) — get a console URL provisioned
- [23-using-the-cli.md](23-using-the-cli.md)
- [24-using-the-python-sdk.md](24-using-the-python-sdk.md)
- [40-realtime-overview.md](40-realtime-overview.md) — the protocols the live page uses
