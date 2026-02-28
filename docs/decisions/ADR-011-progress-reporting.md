# ADR-011: Progress Reporting for Long-Running Engines

## Status

Proposed

## Context

Dalston's batch pipeline expands jobs into a DAG of tasks (prepare, transcribe, align, diarize, PII detect, audio redact, merge). Several of these stages are genuinely long-running — transcription of a 2-hour file can take 10-20 minutes even on GPU, diarization can take longer. Today, clients have no visibility into progress *within* a running task. The API exposes:

- **Job status**: `PENDING → RUNNING → COMPLETED/FAILED`
- **Task status**: `PENDING → READY → RUNNING → COMPLETED/FAILED`
- **Stage breakdown**: which task is running, when it started, its duration

But once a task enters `RUNNING`, it's a black box until it completes or fails. Clients polling `GET /v1/audio/transcriptions/{job_id}` see "transcribe: running" for minutes with no indication of whether it's 5% or 95% done.

This is a problem for three audiences:

1. **CLI users** watching `dalston transcribe` — they want a progress bar
2. **SDK integrators** building UIs — they want to show progress to their end users
3. **Web console operators** — they want a pipeline timeline that fills up as work proceeds

### What Engines Can Report

Not all engines can report progress equally:

| Engine | Can Report Progress? | Granularity |
|--------|---------------------|-------------|
| **Transcription** (Whisper, Parakeet) | Yes — processes audio sequentially | Seconds processed vs. total duration |
| **Alignment** (WhisperX) | Partial — batch processing of segments | Segments aligned vs. total |
| **Diarization** (pyannote) | Limited — internal pipeline stages | Coarse stages (embedding, clustering) |
| **PII Detection** | Yes — processes segments sequentially | Segments scanned vs. total |
| **Audio Redaction** | Yes — processes redaction regions | Regions processed vs. total |
| **Prepare** | Minimal — single FFmpeg operation | Not meaningful (fast) |
| **Merge** | Minimal — JSON assembly | Not meaningful (fast) |

The key observation: **progress granularity is engine-specific and optional**. The system must work well when engines report no progress at all.

## Competitor Analysis

### AssemblyAI

- **Mechanism**: Polling (`GET /v2/transcript/{id}`) or webhooks
- **Progress**: Returns `status` field (`queued`, `processing`, `completed`, `error`). No percentage or ETA within `processing`
- **Webhook**: Single callback on completion/error — no intermediate progress webhooks
- **Notable**: They compensate with very fast processing (near-real-time) so progress is less critical

### Deepgram

- **Batch**: Synchronous-style API — the HTTP request blocks until transcription completes. No progress reporting at all; the trade-off is simplicity
- **Pre-recorded callback**: Optional `callback` URL receives results when done — binary done/not-done
- **Real-time**: WebSocket streaming with interim results (similar to Dalston's real-time mode)
- **Notable**: By making batch synchronous (with long timeouts), they avoid the progress problem entirely. Works for short audio; problematic for long files

### Google Cloud Speech-to-Text

- **Mechanism**: Long Running Operation (LRO) pattern via `operations.get()`
- **Progress**: Returns `metadata.progressPercent` (0-100) updated periodically. Also includes `metadata.lastUpdateTime` and `metadata.startTime`
- **Delivery**: Polling only — no push mechanism for LRO progress
- **Notable**: The LRO pattern is a GCP-wide convention. Progress percentage is best-effort and may jump non-linearly. The `metadata` field is operation-type-specific, allowing rich status info

### AWS Transcribe

- **Mechanism**: Polling (`GetTranscriptionJob`) or EventBridge events
- **Progress**: Status only (`QUEUED`, `IN_PROGRESS`, `COMPLETED`, `FAILED`). No percentage
- **Delivery**: EventBridge publishes `TranscribeJobStateChange` events on status transitions
- **Notable**: Uses EventBridge (a general-purpose event bus) rather than direct webhooks — more powerful but requires AWS infrastructure. The `QUEUED` state is explicitly surfaced, useful when queue depth matters. Built-in CLI waiter polls every 10s with 30-minute timeout

### Rev.ai

- **Mechanism**: Polling (`GET /speechtotext/v1/jobs/{id}`) or webhooks
- **Progress**: Status only (`in_progress`, `transcribed`, `failed`). No percentage
- **Webhook**: Single callback on completion
- **Notable**: Similar to AssemblyAI — fast enough that percentage is less critical

### ElevenLabs Speech-to-Text

- **Mechanism**: Polling or webhooks for batch; WebSocket for real-time
- **Progress**: Binary status (`pending`, `processing`, `completed`, `failed`). No percentage
- **Notable**: Since Dalston aims for ElevenLabs API compatibility, the ElevenLabs-compatible endpoints don't need progress — but Dalston-native endpoints can go further

### Summary Table

| Provider | Progress % | Push Updates | ETA |
|----------|-----------|-------------|-----|
| AssemblyAI | No | Webhook (completion only) | No |
| Deepgram | No (sync) | Callback (completion only) | No |
| Google Cloud | **Yes (0-100)** | No (polling only) | No |
| AWS Transcribe | No | EventBridge (state changes) | No |
| Rev.ai | No | Webhook (completion only) | No |
| ElevenLabs | No | Webhook (completion only) | No |
| OpenAI Whisper | No (sync, 25MB limit) | No | No |

**Takeaway**: Google Cloud is the only provider that reports intra-task progress (percentage via LRO polling). Everyone else reports only discrete status transitions. No one provides real-time streaming of batch progress. No one provides ETA. No one surfaces pipeline stage information. There is a significant opportunity to differentiate.

## Design Options

### Option 1: Polling-Only Progress (Google/AWS Style)

Engines report progress to Redis. The existing `GET /v1/audio/transcriptions/{job_id}` response is extended with progress fields.

#### Engine SDK Change

Add a `report_progress` callback to `TaskInput` or provide it as a method on a context object:

```python
class Engine(ABC):
    @abstractmethod
    def process(self, input: TaskInput) -> TaskOutput:
        ...

# Engine author writes:
class MyTranscriber(Engine):
    def process(self, input: TaskInput) -> TaskOutput:
        for i, chunk in enumerate(chunks):
            result = self.model.transcribe(chunk)
            input.report_progress(
                percent=int((i + 1) / len(chunks) * 100),
                message=f"Transcribed {i+1}/{len(chunks)} chunks",
            )
        return TaskOutput(data=result)
```

#### Transport

The runner writes progress to a Redis hash:

```
HSET dalston:task:{task_id}:progress  percent 42  message "Transcribed 3/7 chunks"  updated_at "2026-02-28T..."
EXPIRE dalston:task:{task_id}:progress 3600
```

The gateway reads this hash when serving the job status endpoint.

#### API Response Extension

```json
{
  "stages": [
    {
      "stage": "transcribe",
      "status": "running",
      "progress": {
        "percent": 42,
        "message": "Transcribed 3/7 chunks",
        "updated_at": "2026-02-28T00:15:30Z"
      }
    }
  ]
}
```

#### Pros

- Simplest to implement — extend existing polling pattern
- No new transport infrastructure (Redis hash is lightweight)
- Backward-compatible — `progress` field is null when engine doesn't report
- Matches industry standard (Google, AWS)
- Engine authors opt in gradually — no forced migration
- No new gateway endpoints

#### Cons

- Clients must poll to see updates — no push notification
- Polling frequency trade-off: too slow = stale progress, too fast = wasted requests
- Not suitable for real-time progress bars without rapid polling
- Progress data is ephemeral (TTL) — not persisted to PostgreSQL

---

### Option 2: Server-Sent Events (SSE) Stream

Add a new endpoint that streams progress events as they occur.

#### New Endpoint

```
GET /v1/audio/transcriptions/{job_id}/progress
Accept: text/event-stream
```

#### Event Stream

```
event: task.progress
data: {"task_id": "...", "stage": "transcribe", "percent": 42, "message": "..."}

event: task.completed
data: {"task_id": "...", "stage": "transcribe"}

event: task.started
data: {"task_id": "...", "stage": "diarize"}

event: task.progress
data: {"task_id": "...", "stage": "diarize", "percent": 10}

event: job.completed
data: {"job_id": "..."}
```

#### Transport (Engine → Gateway)

Engines publish progress via Redis pub/sub:

```python
# In EngineRunner
redis.publish(f"dalston:progress:{job_id}", json.dumps({
    "type": "task.progress",
    "task_id": task_id,
    "percent": 42,
    "message": "Transcribed 3/7 chunks",
}))
```

The gateway subscribes to `dalston:progress:{job_id}` per SSE connection and forwards events.

#### Pros

- Real-time push — no polling, instant updates
- Low latency — events arrive within milliseconds of engine reporting
- Efficient — no wasted requests, server pushes only when there's news
- Works through HTTP/1.1 (no WebSocket upgrade needed)
- Native browser support via `EventSource` API
- Can include stage transitions, not just intra-task progress
- Clean event model — each event is self-contained

#### Cons

- New endpoint and connection model — more gateway complexity
- SSE is unidirectional (server→client) — client can't send messages back (but doesn't need to for progress)
- Connection management: reconnection logic, last-event-id tracking
- Redis pub/sub is fire-and-forget — if gateway reconnects, it misses events during the gap
- Scaling: each SSE connection holds a Redis subscription. With many concurrent watchers, pub/sub fan-out grows
- Load balancer configuration may need tuning for long-lived connections
- Not all HTTP clients handle SSE well (some SDK languages have limited support)

---

### Option 3: WebSocket Progress Channel

Extend the existing WebSocket infrastructure to support batch job progress watching.

#### New Endpoint

```
WS /v1/audio/transcriptions/{job_id}/watch
```

Or multiplex on an existing connection with a subscription model:

```json
{"type": "subscribe", "job_id": "..."}
```

#### Message Flow

```json
// Server → Client
{"type": "task.progress", "stage": "transcribe", "percent": 42, "message": "..."}
{"type": "task.completed", "stage": "transcribe", "duration_ms": 45000}
{"type": "task.started", "stage": "diarize"}
{"type": "job.completed"}
```

#### Pros

- Bidirectional — client could send control messages (e.g., cancel)
- Dalston already has WebSocket infrastructure for real-time transcription
- Lower overhead than SSE for high-frequency updates
- Can reuse existing WebSocket protocol patterns (`protocol.py`)

#### Cons

- More complex than SSE for a purely server→client use case
- WebSocket connections are stateful — harder to load-balance
- Requires WebSocket client library in every SDK language
- Dalston's existing WebSocket is for *audio streaming*, not *event watching* — different patterns
- Browser `WebSocket` API is slightly more complex than `EventSource`
- Overkill for what is essentially a one-way notification stream

---

### Option 4: Webhook Progress Callbacks

Extend the existing webhook system to deliver progress events, not just completion.

#### Configuration

```json
{
  "webhook_url": "https://example.com/hook",
  "events": ["job.completed", "job.failed", "task.progress"]
}
```

#### Delivery

```json
POST https://example.com/hook
{
  "event": "task.progress",
  "job_id": "...",
  "task_id": "...",
  "stage": "transcribe",
  "percent": 42,
  "timestamp": "..."
}
```

#### Pros

- Server-to-server — no open connections to maintain
- Works across network boundaries (firewalls, NATs)
- Decoupled — consumer doesn't need to be connected during processing
- Extends existing webhook infrastructure (ADR-007)
- Good for backend integrators who process results asynchronously

#### Cons

- High frequency progress updates via webhooks = many HTTP requests to customer endpoints
- Customer endpoint must handle rate/volume — burdensome
- Latency: HTTP request per update adds round-trip overhead
- Delivery guarantees: must handle retries, deduplication — complex for high-volume events
- Not suitable for UI progress bars (too indirect)
- Webhook endpoints must be internet-reachable — doesn't work for local CLI usage
- Cost: outbound HTTP requests at scale are expensive

---

### Option 5: Hybrid — Polling + SSE (Recommended)

Combine Options 1 and 2: progress data is always available via polling, with an optional SSE stream for real-time consumers.

#### Architecture

```
Engine.process()
    ↓ report_progress()
EngineRunner
    ↓ writes to Redis hash (for polling) + publishes to Redis pub/sub (for SSE)
    ↓
    ├── GET /v1/audio/transcriptions/{job_id}     ← reads Redis hash → progress in response
    └── GET /v1/audio/transcriptions/{job_id}/events  ← subscribes to pub/sub → SSE stream
```

#### Engine SDK Contract

```python
class ProgressReporter(Protocol):
    def report_progress(
        self,
        percent: int | None = None,
        message: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None: ...

class TaskInput:
    task_id: str
    job_id: str
    audio_path: Path
    progress: ProgressReporter  # NEW — injected by runner
    ...
```

The runner throttles `report_progress` calls to at most once per second to prevent Redis flooding from chatty engines.

#### SSE Endpoint

```
GET /v1/audio/transcriptions/{job_id}/events
Accept: text/event-stream
```

On connection, the gateway:
1. Reads current job state from DB + current progress from Redis → sends initial snapshot
2. Subscribes to `dalston:progress:{job_id}` pub/sub channel
3. Forwards events as SSE

This solves the "missed events during reconnect" problem — the client always gets the current state first, then live updates.

#### Consistency Model

- Redis hash is the **source of truth** for current progress (survives pub/sub message loss)
- Pub/sub is the **notification mechanism** (low-latency push)
- On SSE reconnect: gateway reads hash for current state, then resumes pub/sub subscription
- On job completion: hash is deleted (TTL), final state is in PostgreSQL

#### Event Types

```
event: job.status
data: {"status": "running", "started_at": "..."}

event: task.started
data: {"task_id": "...", "stage": "transcribe", "engine_id": "faster-whisper-base"}

event: task.progress
data: {"task_id": "...", "stage": "transcribe", "percent": 42, "message": "Transcribed 3/7 chunks"}

event: task.completed
data: {"task_id": "...", "stage": "transcribe", "duration_ms": 45230}

event: task.failed
data: {"task_id": "...", "stage": "transcribe", "error": "CUDA OOM"}

event: job.completed
data: {}

event: job.failed
data: {"error": "Transcription failed after 3 retries"}
```

#### Pros

- **Two consumption modes**: simple polling for basic clients, SSE for rich UIs
- **Graceful degradation**: if SSE isn't available (proxy issues, client limitations), polling still works
- **Consistency**: snapshot-on-connect prevents missed events
- **Efficiency**: polling clients get progress without new endpoints; streaming clients get push without polling
- **Engine flexibility**: `report_progress` is optional — engines that don't call it simply show no intra-task progress
- **Throttling**: runner-level rate limiting protects Redis from chatty engines

#### Cons

- Two mechanisms to maintain (hash + pub/sub + SSE endpoint)
- Most complex option to implement
- Must handle SSE connection lifecycle (reconnection, keepalive, timeouts)
- Redis pub/sub per-job: need to manage subscription lifecycle carefully

## Recommendation

**Option 5 (Hybrid Polling + SSE)** — with a phased rollout:

### Phase 1: Polling Progress (minimal, high value)

1. Add `ProgressReporter` protocol to engine SDK
2. Add `progress` field to `TaskInput` (injected by runner)
3. Runner writes progress to Redis hash with throttling
4. Extend `GET /v1/audio/transcriptions/{job_id}` to include progress on running stages
5. Update CLI and SDK to display progress when polling

This alone matches Google Cloud's LRO pattern, which is the industry ceiling today. Adding stage-level progress on top (which no competitor does) would be a clear differentiator.

### Phase 2: SSE Stream (for real-time UIs)

1. Add `GET /v1/audio/transcriptions/{job_id}/events` SSE endpoint
2. Gateway subscribes to Redis pub/sub, forwards as SSE
3. Snapshot-on-connect for consistency
4. Update web console to use SSE for live pipeline timeline
5. Add SSE support to Python SDK

### Phase 3: Engine Adoption

Roll out `report_progress` calls in engines, starting with the highest-value ones:
1. Transcription engines (Whisper, Parakeet) — process audio sequentially, easy to report
2. PII detection — processes segments sequentially
3. Diarization — coarse stage reporting
4. Alignment — segment batch progress

### Non-Goals

- **WebSocket for progress**: Overkill for one-way events. SSE is simpler and sufficient
- **Webhook progress callbacks**: Too expensive at scale for high-frequency updates. Webhooks remain for completion/failure only
- **ETA prediction**: Tempting but unreliable. Progress percentage + audio duration lets clients estimate locally if they want
- **Persisting progress history to PostgreSQL**: Ephemeral data — not worth the write amplification. Redis TTL is sufficient
- **Progress on ElevenLabs-compatible endpoints**: ElevenLabs doesn't expose progress, so `/v1/speech-to-text/*` endpoints stay unchanged. Progress is a Dalston-native extension only

## Consequences

### Easier

- **CLI progress bars**: `dalston transcribe` can show `Transcribing... 42% [████░░░░░░] 3/7 chunks`
- **Web console pipeline view**: Live-updating timeline showing each stage filling up
- **SDK integrations**: Developers can build progress UIs for their end users
- **Debugging slow jobs**: See exactly where time is being spent within a stage
- **Competitive differentiation**: Better progress visibility than any current competitor

### Harder

- **Engine SDK surface area**: Engine authors have a new (optional) API to learn
- **Redis memory**: Progress hashes consume memory (mitigated by TTL and small payload)
- **SSE infrastructure**: Gateway must manage long-lived connections and Redis subscriptions
- **Testing**: Need integration tests for progress propagation end-to-end

### Mitigations

- `report_progress` is fully optional — existing engines work unchanged
- Runner throttles to max 1 progress update/second — bounded Redis overhead
- SSE connections have keepalive and idle timeout — no resource leaks
- Progress hash TTL matches task timeout — automatic cleanup
