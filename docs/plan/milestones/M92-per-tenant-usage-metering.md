# M92: Per-Tenant Usage Metering and Billing Reports

|                    |                                                              |
| ------------------ | ------------------------------------------------------------ |
| **Goal**           | A durable per-tenant ledger of every transcribed audio second, broken down by engine, stage, and realtime-vs-batch, exposed via a stable API that reseller tenants can bill their own customers from |
| **Duration**       | 6–9 days                                                     |
| **Dependencies**   | M11 (API authentication — complete, provides tenant scoping), M18 (unified structured logging — complete), M76 (engine telemetry depth — complete, provides per-engine timings) |
| **Deliverable**    | `usage_events` table + migration, synchronous ledger writes at task/session end, `GET /v1/usage`, `GET /v1/usage/export.csv`, console usage page |
| **Status**         | Not Started                                                  |

## User Story

> *"As a company building a transcription product on top of Dalston, I want a monthly usage report per customer that tells me exactly how many audio-seconds they transcribed, split by batch vs realtime, with per-engine timings, so I can invoice them — and I want the same data exposed as an API so I can wire it into my billing system without scraping logs."*

---

## Outcomes

| Scenario | Current | After M92 |
| -------- | ------- | --------- |
| Reseller needs monthly usage per customer | Must grep correlation IDs out of structured logs and aggregate by hand | `GET /v1/usage?tenant=...&window=2026-03` returns a clean JSON breakdown |
| Heavy tenant complains "my bill seems high" | No tenant-facing self-service view | Console usage page shows last 30 days with drill-down to individual jobs |
| Finance team does quarterly reconciliation | Guess from log aggregates | CSV export matches Postgres ledger to the second |
| Tenant tries to audit their own usage | No API to do it | Same `GET /v1/usage` endpoint, scoped to the caller's tenant by the auth middleware |
| ElevenLabs Jan-14 2026-style "inflated per-request charges" incident | Silent; no way to verify server-side counters | Ledger is the source of truth and is independently queryable — overcharges become auditable |

---

## Motivation

The loudest non-technical complaint about ElevenLabs Scribe in 2026 isn't accuracy — it's **pricing trust**. Concrete signals:

- Users reporting their "effective" cost is ~2.8× the advertised per-character rate because of failed generations being counted.
- A documented 2026-01-14 incident where STT requests with certain User-Agent headers were charged inflated per-request amounts until caught.
- Comparison posts calling out ElevenLabs' "opaque credit-based pricing" versus competitors' transparent per-minute billing.
- BBB complaints about unauthorized auto-renewal and credits deducted instead of rolled over.

Dalston's positioning advantage is that the tenant owns the infrastructure — there is literally no vendor bill to dispute. But that advantage only lands for **direct operators**. For **resellers building on top of Dalston**, they still need to bill their own customers, and today Dalston does not give them a first-class way to measure.

The logs have the data (every task carries `tenant_id`, `engine_id`, `audio_duration_s`, `wall_time_s`). The gap is **durability, query-ability, and a stable contract**. Logs aren't a billing source of truth.

M92 builds a dedicated `usage_events` ledger table with synchronous writes on task completion, exposes it through a clean API, and makes it the canonical source of truth for "how much STT did this tenant consume this month". Positioning: **transparent to the second**, per-tenant, exportable, auditable.

---

## Architecture

```
┌──────────────────────────────────────────────────────────────────────┐
│                    USAGE LEDGER WRITE PATH                            │
│                                                                       │
│   batch task completes                                                │
│        │                                                              │
│        ▼                                                              │
│   ┌──────────────────────────┐                                       │
│   │  orchestrator finalizer  │  existing code path                    │
│   │  on_task_end(task)       │                                       │
│   └──────────┬───────────────┘                                       │
│              │                                                        │
│              ▼                                                        │
│   ┌──────────────────────────┐                                       │
│   │  UsageLedger.record(...)  │  NEW                                  │
│   │                           │                                       │
│   │  INSERT INTO usage_events │                                       │
│   │  (tenant_id, job_id,      │                                       │
│   │   stage, engine_id,       │                                       │
│   │   audio_duration_s,       │                                       │
│   │   wall_duration_s,        │                                       │
│   │   billing_mode, ts, …)    │                                       │
│   └──────────┬───────────────┘                                       │
│              │ synchronous — ledger write is part of the task       │
│              │ commit, not a best-effort log                          │
│              ▼                                                        │
│   ┌──────────────────────────┐                                       │
│   │  Postgres usage_events    │                                       │
│   │  (partitioned by month)   │                                       │
│   └──────────────────────────┘                                       │
│                                                                       │
│   realtime session ends                                               │
│        │                                                              │
│        ▼                                                              │
│   SessionHandler.on_end()                                             │
│        └──▶ UsageLedger.record(session-shaped event)                  │
└──────────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────────┐
│                    USAGE READ PATH                                    │
│                                                                       │
│   GET /v1/usage ──▶ usage_service.aggregate(tenant, window, group_by) │
│                         │                                             │
│                         ▼                                             │
│                    SQL rollup over usage_events                       │
│                         │                                             │
│                         ▼                                             │
│                    {by_engine, by_day, by_mode, total}                │
└──────────────────────────────────────────────────────────────────────┘
```

---

## Steps

### 92.1: `usage_events` table + migration

**Files modified:**

- `dalston/db/migrations/<next>_usage_events.py` *(new)*
- `dalston/db/models/usage.py` *(new)*
- `tests/unit/test_usage_models.py` *(new)*

**Deliverables:**

A new append-only table. Per CLAUDE.md: migrations append-only, no cascade delete, pagination on list endpoints. The table is partitioned by month to keep monthly rollups fast and old-partition drops clean.

```sql
CREATE TABLE usage_events (
    id              BIGSERIAL PRIMARY KEY,
    event_id        UUID NOT NULL UNIQUE,   -- idempotency key for writes
    tenant_id       UUID NOT NULL,
    actor_id        UUID NOT NULL,          -- api key that made the request
    job_id          UUID NULL,              -- batch job, NULL for realtime
    session_id      UUID NULL,              -- realtime session, NULL for batch
    stage           TEXT NOT NULL,          -- 'transcribe', 'diarize', 'refine', 'realtime', ...
    engine_id       TEXT NOT NULL,          -- 'faster-whisper-large-v3', 'pyannote-4.0', ...
    billing_mode    TEXT NOT NULL,          -- 'batch' | 'realtime'
    audio_duration_s DOUBLE PRECISION NOT NULL,
    wall_duration_s  DOUBLE PRECISION NOT NULL,
    status          TEXT NOT NULL,          -- 'success' | 'failed'
    ts              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    metadata        JSONB NULL              -- free-form, non-billable extras
) PARTITION BY RANGE (ts);

CREATE INDEX ix_usage_events_tenant_ts ON usage_events (tenant_id, ts);
CREATE INDEX ix_usage_events_job       ON usage_events (job_id) WHERE job_id IS NOT NULL;
CREATE INDEX ix_usage_events_session   ON usage_events (session_id) WHERE session_id IS NOT NULL;
```

**Partitioning:** monthly range partitions. A small cron (or Postgres `pg_partman`) creates next month's partition during the last week of the current month. Old partitions can be archived/dropped by operators per the retention policy.

**Retention:** default retention is 13 months (12 billing months + a grace month for late-arriving events / disputes). Configurable via `DALSTON_USAGE_RETENTION_MONTHS`.

**`event_id` idempotency:** the writer generates `event_id = uuid5(namespace, f"{job_id}:{stage}:{engine_id}")` so re-driving a finished task does not double-count usage. `UNIQUE` constraint enforces it.

**Failed tasks:** written with `status='failed'` and `audio_duration_s=0.0`. They exist in the ledger for observability (count of failures per tenant) but do not contribute to billing aggregates. This is explicit: **Dalston does not charge for failed transcriptions.** That is the wedge against ElevenLabs' "2.8× effective cost" complaint.

---

### 92.2: `UsageLedger` write service

**Files modified:**

- `dalston/gateway/services/usage_ledger.py` *(new)*
- `dalston/orchestrator/finalizer.py` — call `UsageLedger.record` at task end
- `dalston/gateway/api/v1/realtime.py` — call at session end
- `dalston/gateway/api/v1/elevenlabs_stt.py` — same
- `tests/unit/test_usage_ledger.py` *(new)*

**Deliverables:**

A thin service that wraps the Postgres insert. Synchronous in the task-completion transaction so the ledger is consistent with job state. The write is **not** async-fire-and-forget — if the insert fails, the task completion itself fails and retries normally.

```python
# dalston/gateway/services/usage_ledger.py

@dataclass
class UsageEvent:
    tenant_id: UUID
    actor_id: UUID
    job_id: UUID | None
    session_id: UUID | None
    stage: str
    engine_id: str
    billing_mode: Literal["batch", "realtime"]
    audio_duration_s: float
    wall_duration_s: float
    status: Literal["success", "failed"]
    metadata: dict | None = None

class UsageLedger:
    def __init__(self, session_factory) -> None: ...

    async def record(self, event: UsageEvent) -> None:
        """Idempotent insert. Re-records return silently."""
        ...

    async def record_batch(self, events: list[UsageEvent]) -> None:
        """Single transaction for all events from one task finalizer call."""
        ...
```

**Per-stage granularity:** each stage emits its own event. A batch job with TRANSCRIBE + ALIGN + DIARIZE produces 3 events. That's deliberate: operators can see which stage is consuming the most compute (wall_duration_s) and bill differently per stage if they want.

**Realtime accounting:** a realtime session produces one event at session end with `billing_mode='realtime'`, `audio_duration_s` = total audio streamed, `wall_duration_s` = session wall clock. Session persistence (M24) already tracks both.

---

### 92.3: `GET /v1/usage` read API

**Files modified:**

- `dalston/gateway/api/v1/usage.py` *(new)*
- `dalston/gateway/services/usage_service.py` *(new)* — aggregation queries
- `dalston/schemas/usage.py` *(new)*
- `tests/integration/test_usage_api.py` *(new)*

**Deliverables:**

```
GET /v1/usage
    ?window=2026-03
    ?start=2026-03-01T00:00:00Z&end=2026-04-01T00:00:00Z
    ?group_by=engine,day,mode
    ?tenant=<uuid>    # admin-only; callers default to their own tenant
```

Response:

```json
{
  "window": { "start": "2026-03-01T00:00:00Z", "end": "2026-04-01T00:00:00Z" },
  "tenant_id": "...",
  "total": {
    "audio_duration_s": 184320.5,
    "wall_duration_s": 67241.1,
    "events": 4821,
    "failed_events": 17
  },
  "by_mode": {
    "batch":    { "audio_duration_s": 120480.0, "events": 1240 },
    "realtime": { "audio_duration_s":  63840.5, "events": 3564 }
  },
  "by_engine": [
    { "engine_id": "faster-whisper-large-v3", "audio_duration_s": 98100.0, ... },
    { "engine_id": "pyannote-4.0",            "audio_duration_s": 67210.0, ... }
  ],
  "by_day": [
    { "date": "2026-03-01", "audio_duration_s": 6132.4 },
    ...
  ]
}
```

**Auth scoping:** non-admin callers are always scoped to their own tenant, regardless of the `?tenant=` param. Admin callers can pass `?tenant=<uuid>` to query other tenants. This is enforced in the auth middleware layer, not in the handler.

**Pagination:** `group_by` aggregates cap at 10k groups (CLAUDE.md: "pagination on all list endpoints"). Drill-down into individual events uses a separate `GET /v1/usage/events?cursor=...&limit=...` endpoint with cursor pagination on `(ts DESC, id DESC)`.

**Performance:** rollups hit the monthly partition indexed on `(tenant_id, ts)`. A month of data for a busy tenant is ~100k events. Target: p95 under 200 ms for a monthly rollup query on a warm index.

---

### 92.4: CSV export

**Files modified:**

- `dalston/gateway/api/v1/usage.py` — add `GET /v1/usage/export.csv`
- `tests/integration/test_usage_export.py` *(new)*

**Deliverables:**

```
GET /v1/usage/export.csv
    ?window=2026-03
    ?format=flat|aggregated
```

- `format=flat` — one row per event. Good for pushing into a spreadsheet or external billing system. Streaming response, no full-buffer in memory.
- `format=aggregated` — one row per `(tenant, engine, day)`. Smaller, suitable for email attachments.

CSV columns are stable and documented in `docs/specs/usage/CSV_FORMAT.md`. Breaking the format is a contract change requiring a major version bump.

Streaming uses the same pattern as the existing audit log export — no full materialization in memory, OK for tenants with millions of events.

---

### 92.5: Console usage page

**Files modified:**

- `web/src/pages/Usage.tsx` *(new)*
- `web/src/api/usage.ts` *(new)*
- `dalston/gateway/api/console/usage.py` *(new)*

**Deliverables:**

A `/usage` page in the management console showing:

1. **Summary card:** total audio seconds this month, % vs last month, failed-event count.
2. **Stacked bar chart:** audio seconds per day, stacked by engine.
3. **Pie:** batch vs realtime split.
4. **Table:** top 20 tenants by audio-seconds (admin only) or own usage (non-admin).
5. **Drill-down:** click a day → see the individual jobs/sessions from that day with links to the job detail page.
6. **CSV download:** button that calls `GET /v1/usage/export.csv` with the currently selected window.

Auth-gated exactly like the existing jobs page.

---

### 92.6: Backfill script (optional)

**Files modified:**

- `dalston/tools/usage_backfill.py` *(new)*

**Deliverables:**

A one-shot script that reads past completed jobs from the `jobs` / `realtime_sessions` tables and writes synthetic `usage_events` rows for them. Idempotent via the deterministic `event_id`. Useful for operators upgrading from pre-M92 so their historical data isn't lost.

**Scope:** backfill reads from existing durable tables only. It does **not** replay structured logs — that would require log retention that most deployments won't have.

Documented as a one-off operator action, not part of normal operation.

---

## Non-Goals

- **Billing itself.** M92 is a metering ledger. It does not generate invoices, apply pricing, handle currency, or integrate with Stripe/etc. Resellers layer their own pricing on top of the ledger data. The justification: every reseller has a different price structure and bundling philosophy; building a billing engine into Dalston would impose choices on all of them.
- **Per-request cost estimation before the request runs.** "How much will this job cost?" is a pricing question, not a metering question. Out of scope.
- **Real-time usage throttling.** Enforcing quota caps in the hot path is a separate concern that needs different data (running counters in Redis, not durable Postgres events). Tracked in a follow-up.
- **Retention beyond 13 months.** Operators who need longer history can export the CSV and store it elsewhere. The ledger is a billing source of truth, not a permanent archive.
- **PII in the ledger.** Events carry IDs and timings only. No transcript text, no audio paths, no user content. This is deliberate so the ledger can be queried freely by ops without data-classification concerns.
- **Per-chunk events for chunked transcription (M86).** M86 chunking produces one `engine.chunked_request` span but emits N inference spans. The ledger writes **one** aggregate event per task (the wrapper span), not per chunk. Per-chunk granularity goes into `metadata.chunks` as an integer count if callers want it.

---

## Deployment

Append-only migration. Existing routes are unchanged. The ledger write is new code on the task-completion path; a bug there could fail tasks, so 92.2 ships behind a kill switch:

```bash
DALSTON_USAGE_LEDGER_ENABLED=true
```

With the switch off, `UsageLedger.record` is a no-op and the system behaves exactly as today. Flip on in staging, watch for insert errors, roll out to prod. After a week of clean operation, remove the kill switch in a follow-up PR.

**Migration ordering:** the partitioned table must exist before the service tries to write. Standard migration-before-deploy ordering applies.

**Backfill (92.6):** only run in environments where historical data matters. Purely additive; safe to run multiple times.

---

## Verification

```bash
make dev

# 1. Submit a batch job, confirm a ledger row is written per stage
JOB=$(curl -s -X POST http://localhost:8000/v1/audio/transcriptions \
  -H "Authorization: Bearer $DALSTON_API_KEY" \
  -F "file=@tests/fixtures/audio/short-sample.wav" -F "diarize=true" \
  | jq -r .job_id)

# Wait for completion, then query the ledger:
psql $DALSTON_DATABASE_URL -c "
  SELECT stage, engine_id, audio_duration_s, wall_duration_s, status
  FROM usage_events WHERE job_id = '$JOB' ORDER BY ts;
"
# Expected: rows for transcribe, diarize, merge (minimum), all success

# 2. Monthly rollup for the current tenant
curl -s "http://localhost:8000/v1/usage?window=$(date +%Y-%m)" \
  -H "Authorization: Bearer $DALSTON_API_KEY" | jq '.total, .by_mode'

# 3. Realtime session produces one event at end
python scripts/test_elevenlabs_realtime.py \
  --audio tests/fixtures/audio/30s-speech.wav
# Then:
psql $DALSTON_DATABASE_URL -c "
  SELECT billing_mode, audio_duration_s, wall_duration_s
  FROM usage_events
  WHERE tenant_id = '...' AND billing_mode = 'realtime'
  ORDER BY ts DESC LIMIT 1;
"

# 4. Failed task is recorded as status='failed', audio_duration_s=0
#    (use an audio file that will fail deliberately)
curl -X POST http://localhost:8000/v1/audio/transcriptions \
  -H "Authorization: Bearer $DALSTON_API_KEY" \
  -F "file=@tests/fixtures/audio/corrupt.wav"
# Confirm row exists with status='failed' and audio_duration_s=0

# 5. CSV export
curl -s "http://localhost:8000/v1/usage/export.csv?window=2026-04&format=aggregated" \
  -H "Authorization: Bearer $DALSTON_API_KEY" \
  | head

# 6. Idempotency — replay same event_id
python -m pytest tests/unit/test_usage_ledger.py::test_idempotent_record -v
```

---

## Checkpoint

- [ ] **92.1** `usage_events` table with monthly partitioning
- [ ] **92.1** `event_id` UNIQUE constraint for idempotency
- [ ] **92.1** Indexes on `(tenant_id, ts)` and on `job_id`, `session_id`
- [ ] **92.2** `UsageLedger.record` + `record_batch` synchronous writes
- [ ] **92.2** Task finalizer emits one event per stage
- [ ] **92.2** Realtime session end emits one event
- [ ] **92.2** Failed tasks recorded with `status='failed'`, `audio_duration_s=0`
- [ ] **92.3** `GET /v1/usage` with `window`, `group_by`, `tenant` params
- [ ] **92.3** Non-admin callers scoped to own tenant by auth middleware
- [ ] **92.3** p95 monthly rollup < 200 ms on a warm index
- [ ] **92.4** `GET /v1/usage/export.csv` with `flat` and `aggregated` formats, streamed
- [ ] **92.5** Console `/usage` page with daily chart, batch/realtime split, CSV download
- [ ] **92.6** Backfill script (optional, one-shot)
- [ ] Kill switch `DALSTON_USAGE_LEDGER_ENABLED` defaults off until verified in staging
- [ ] No regression on task finalizer throughput (benchmark before/after)
