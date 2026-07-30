# M95: Autoscaler Console Visibility & Knobs

| | |
| --- | --- |
| **Goal** | Surface autoscaler state (backlog, desired vs live, spot failures) on the Infrastructure page and expose the core scaling knobs in Settings → Autoscale |
| **Duration** | 4–6 days |
| **Dependencies** | M91 (Queue-based GPU autoscaling), M78 (Infrastructure topology view), M35 (Settings page) |
| **Deliverable** | Per-tick decision snapshot in Redis, `autoscaler` block in `/api/console/nodes`, autoscaler strip on the Infra page, `autoscale` settings namespace with nullable Redis-mirrored overrides, opt-in on-demand fallback |
| **Status** | Not Started |

## User Story

> *"As an operator, I want to see why the autoscaler is or isn't scaling and tune its thresholds from the console, so that I don't have to SSH into the control plane and read journalctl to understand or adjust GPU capacity."*

---

## Outcomes

| Scenario | Current | After M95 |
| -------- | ------- | ---------- |
| 41 tasks queued, 2 instances live, 1 booting | Infra page shows 2 node cards + 1 booting card; no hint of why | Strip shows `● ● ◐ ○ ○ · Backlog 41 ÷ 20/instance → wants 3` |
| Fleet idle, scale-down cooldown running | Nothing; instances just disappear ~35 min later | Strip shows `idle · terminating 1 in 12m` |
| Spot quota reached while backlog grows | Tick audits to a control-plane file and returns; console shows nothing; jobs fail after their wait deadline with no warning | Warning on the strip: `⚠ at spot quota — backlog 41, retrying every minute`; escalates to red with `earliest waiting task may fail in ~18m` |
| Autoscale timer dies while workers run | Nothing distinguishes silence from health | Strip shows `autoscaler not reporting since 12:04 — check dalston-autoscale.timer` |
| Operator wants scaling twice as aggressive | SSH to control plane, edit `/data/dalston/autoscale-policy.yaml` | Settings → Autoscale → "Tasks per instance" 20 → 10; live within one tick |
| Operator wants a warm instance always on | Edit YAML `min_instances: 1` by hand | Settings → Autoscale → "Minimum instances" = 1; unset knobs display `Inherited from control plane: 3` |
| Spot capacity drought for >5 min | Backlog waits until jobs fail | (Opt-in) autoscaler launches one on-demand instance and labels it on the strip |

---

## Motivation

M91 made the autoscaler correct; nothing made it *legible*. Every tick computes a rich decision (`ScaleDecision.to_dict()`, `infra/scripts/dalston_autoscale.py:202-216`) — backlog per engine, desired vs live vs pending, a human-readable reason — and then prints it to stdout where only `journalctl -u dalston-autoscale.service` can see it. Spot quota/capacity failures (handled gracefully since cb446f8b) are audited to `~/.dalston/audit.log` on the control plane and surface nowhere else. The worst case is silent: at spot quota with zero live instances, queued jobs wait out their scheduler-assigned deadline (`wait_deadline_at`, `dalston/orchestrator/scheduler.py:368-373`) and then fail, and the console gives no warning at any point.

Meanwhile every scaling knob lives in a YAML file on the control plane, edit-by-SSH, while the console already has a Settings page with namespaced, validated, resettable knobs (M35) — the natural home for `tasks_per_instance` and `min_instances`.

Design constraints honored throughout:

- `dalston-aws` stays dependency-free of the `dalston` package; the bridge is Redis keys with names mirrored in `dalston/common/registry.py`, exactly as M91.7 did for pending nodes.
- The gateway never writes the policy YAML. Settings overrides flow through Redis; YAML remains the seeded base owned by the control plane.
- The tick must never `die()` on a bad override — invalid override values are discarded, audited, **and published** so Settings can show the operator what happened.
- The console must never present a value the autoscaler isn't actually using, and must never confuse "autoscaler silent" with "no autoscaler". Both properties fall out of the tick echoing its effective policy and a persistent configured-shapes marker.

---

## Architecture

```
┌──────────────── CONTROL PLANE ────────────────┐      ┌───────────── GATEWAY ─────────────┐
│                                               │      │                                   │
│  autoscale-policy.yaml (base, seeded)         │      │  Settings service (Postgres)      │
│        │                                      │      │   namespace "autoscale" (nullable)│
│        ▼                                      │      │        │ write + 60s reconcile    │
│  dalston-autoscale tick (60s)                 │      │        ▼                          │
│   1. read policy + overrides ◀────────────────┼──────┼── dalston:autoscale:overrides (H) │
│   2. decide()                                 │      │                                   │
│   3. publish ─┬─▶ dalston:autoscale:tick:<shape>     │  GET /api/console/nodes           │
│               ├─▶ dalston:autoscale:shapes (no TTL)  │   nodes + booting + autoscaler[]  │
│               └─▶ dalston:autoscale:blocked:<shape>  │   + earliest wait deadline        │
│   4. apply (launch/terminate/blocked)         │      └────────────────┬──────────────────┘
│                                               │                       ▼
└───────────────────────────────────────────────┘        Infra page: autoscaler strip
                                                         ● ● ◐ ○ ○  backlog ÷ per-instance
```

---

## Steps

### 95.1: Tick publishes decision snapshot to Redis

**Files modified:**

- `infra/scripts/dalston-aws` — write snapshot, shapes marker, and blocked key each tick
- `infra/scripts/dalston_autoscale.py` — extend `ScaleDecision.to_dict()` payload with policy echo and fleet lifecycle split
- `dalston/common/registry.py` — mirror the new key constants

**Deliverables:**

After computing and applying each shape's decision, `cmd_autoscale` writes three things:

**1. Snapshot** — `dalston:autoscale:tick:<shape>`, `AUTOSCALE_TICK_TTL_S = 3600`. The TTL is deliberately far above the UI's ~3-minute stale threshold: a stale-but-present snapshot is what makes the "not reporting" state renderable at all, and it must outlive the threshold by a wide margin. (Shape removal is handled by the marker rewrite below, not by TTL.)

```json
{
  "schema_version": 1,
  "shape": "nemo+pyannote",
  "ts": "2026-07-30T12:04:00Z",
  "action": "none",
  "applied": "at desired capacity",
  "desired": 3, "live": 2, "pending": 1,
  "spot_live": 2, "on_demand_live": 0,
  "max_backlog": 41,
  "per_engine": {"nemo": {"lag": 41, "in_flight": 4}, "pyannote-4.0": {"lag": 0, "in_flight": 0}},
  "idle_since_s": null,
  "policy": {"tasks_per_instance": 20, "min_instances": 0, "max_instances": 5,
             "scale_down_after_s": 2100,
             "overrides_applied": ["tasks_per_instance"], "override_error": null},
  "blocked": null
}
```

`policy` echoes the *effective* values after overrides (95.5) — the UI shows what the autoscaler actually used, making the settings plumbing self-verifying. `spot_live`/`on_demand_live` come from the `spot` flag fleet discovery already returns per instance (`dalston-aws:2928`, `InstanceLifecycle == "spot"`).

**2. Configured-shapes marker** — `dalston:autoscale:shapes`, a persistent hash (no TTL) of `<shape name> → <last tick ISO timestamp>`, seeded at `--provision` and **rewritten atomically from the current policy every tick** (`MULTI`: `DEL` + `HSET` all shapes + `EXEC`), so a shape removed from the YAML disappears from the console within one tick — the marker mirrors the policy, it doesn't accumulate history. This is how the gateway distinguishes "autoscaler configured but silent" from "no autoscaler on this deployment" — expired snapshots must never be read as absence. Deprovisioning cleanup is explicit: when the autoscaler is torn down (timer + service removal path in `dalston-aws`), delete the marker plus all `tick:*` / `blocked:*` / `state:*` keys, so the console reverts to "no autoscaler" rather than reporting a permanently-stale ghost.

**3. Blocked state** — `dalston:autoscale:blocked:<shape>`, a **dedicated key**, hash fields `kind` (`spot_quota` | `spot_capacity`), `since`, `ticks`, TTL 3600s refreshed on each blocked tick, deleted on any successful launch or any tick that needs no launch. It cannot live in the existing `dalston:autoscale:state:<shape>` hash: `_update_idle_state` **deletes that entire hash whenever backlog exists** (`dalston-aws:3273`) — which is exactly when blocked tracking matters, so a shared hash would zero the counter every tick. Set when `_apply_decision` catches `SpotQuotaError` / `SpotCapacityError`, and reflected into the snapshot:

```json
"blocked": {"kind": "spot_quota", "since": "2026-07-30T11:58:00Z", "consecutive_ticks": 6, "detail": "..."}
```

Dry-run uses the existing shadow prefix for all three keys.

---

### 95.2: Gateway exposes `autoscaler` block on `/api/console/nodes`

**Files modified:**

- `dalston/gateway/api/console.py` — enumerate shapes from the marker, attach snapshots, derive earliest wait deadline; add `autoscaler: list[AutoscalerShapeView]` to `NodesResponse`
- `web/src/api/types.ts` — TS mirror

**Deliverables:**

`get_nodes` (which already merges `dalston:autoscale:pending:*`) enumerates **`dalston:autoscale:shapes`** — not the snapshot keys — and returns one entry per configured shape:

- Snapshot present and parseable → returned as a typed view, plus `stale: bool` (snapshot `ts` older than 3 min) and `last_tick_at`.
- Snapshot expired → entry with `stale: true`, `last_tick_at` from the marker, and null decision fields. The strip can always render "not reporting since \<time\>".
- Snapshot unparseable or `schema_version` unknown (e.g. control plane updated ahead of the gateway) → **degrade that shape only**: an entry with `stale: true` and null decision fields, warning logged. Per-shape parsing is wrapped so one bad snapshot can never fail typed response validation and blank the entire Infrastructure page.
- Empty/absent marker → empty list → UI renders nothing (local dev unaffected).

**Earliest wait deadline, from the authoritative source.** The countdown must come from real task deadlines, not `blocked.since + timeout`: queued tasks can predate the spot failure or arrive after it, so arithmetic on the blocked timestamp is wrong in both directions. The authoritative store is the task metadata hash: the scheduler stamps `wait_deadline_at` into `dalston:task:<task_id>` (`scheduler.py:352,373`) and adds the task ID to the `dalston:waiting_engine_tasks` set (`scheduler.py:389`), which the scanner and engine SDK remove from on dispatch or timeout — the set is precisely "tasks currently waiting for an engine". (Not the streams: entries carry only `task_id`/`job_id`/`enqueued_at`/`timeout_at` (`streams.py:104-109`), and acknowledged entries remain in the stream, so stream-head reads are wrong on both content and ordering.) The gateway does `SMEMBERS dalston:waiting_engine_tasks`, pipelines `HMGET dalston:task:<id> engine_id wait_deadline_at`, filters to the shape's `stream_engine_ids`, and returns `earliest_wait_deadline_at` (nullable) — the minimum surviving deadline. Null when no waiting task has one; the UI then falls back to showing only how long launching has been blocked.

---

### 95.3: Autoscaler strip on the Infrastructure page

**Files modified:**

- `web/src/pages/Infrastructure.tsx` — `AutoscalerStrip` component above the node grid, one per shape
- `web/src/lib/strings.ts` — copy

**Deliverables:**

```
┌─ Autoscaler · nemo+pyannote ────────────────────────────────────────┐
│  ● ● ◐ ○ ○         live 2 · booting 1 · max 5                       │
│  Backlog 41 ÷ 20 per instance → wants 3                             │
│  Launched i-0abc (g6.xlarge) · 42s ago                              │
└─────────────────────────────────────────────────────────────────────┘
```

- Slot row: `●` live (green), `◐` booting (amber, pulsing — matches the existing `BootingNodeCard` accent), `○` dashed gray up to `max_instances`. Slots, not ghost node cards: at scale-to-zero idle (the normal state) full-size gray cards would dominate the page and read as breakage.
- Idle state collapses to one quiet line: `idle · 0 of 5 · scales up on demand`. Cooldown state shows a countdown derived from `idle_since_s` vs `scale_down_after_s`: `idle · terminating 1 in 12m`.
- Warning state (`blocked` set): amber strip border, `⚠ At spot quota — backlog 41, retrying every minute. Quota frees when a worker terminates.` ("Backlog", not "tasks waiting": `max_backlog` counts lag plus in-flight work, so "waiting" would overstate the queue.)
- Critical state: `blocked` set **and** `live == 0` **and** `max_backlog > 0` — red border. With `earliest_wait_deadline_at`: `Earliest waiting task may fail in ~18m`. Without it: `Spot launching blocked for 12m` — never a fabricated countdown.
- Stale state (`stale: true`): gray strip, `autoscaler not reporting since 12:04 — check dalston-autoscale.timer`. Reachable by construction: the shapes marker has no TTL and snapshots outlive the threshold by ~20×.
- On-demand workers present (`on_demand_live > 0`): `running 1 on-demand — spot unavailable` (95.6).
- Data arrives via the existing `useNodes()` 10s poll; no new hook.

---

### 95.4: `autoscale` settings namespace — nullable, mirrored, reconciled

**Files modified:**

- `dalston/gateway/services/settings.py` — new namespace + 4 **nullable** definitions (`nullable: bool` on `SettingDefinition`); null-means-delete update semantics; sync-to-Redis on write/reset
- `dalston/gateway/main.py` — 60s reconcile loop registered in the lifespan (distributed mode only), cancelled cleanly on shutdown
- `dalston/gateway/api/console.py` — autoscale namespace GET enriched with effective values from tick snapshots; namespace listed only when the shapes marker is non-empty
- `web/src/pages/Settings.tsx` — nullable-field rendering ("Inherited from control plane: N"), override-error banner
- `web/src/api/types.ts` — TS mirror

**Deliverables:**

```python
NamespaceInfo(
    namespace="autoscale",
    label="Autoscale",
    description="GPU autoscaler thresholds (applies to all shapes; unset values inherit the control-plane policy file)",
)
```

| key | type | default | range | label |
| --- | --- | --- | --- | --- |
| `tasks_per_instance` | int? | *unset* | 1–500 | Tasks per instance |
| `min_instances` | int? | *unset* | 0–10 | Minimum instances |
| `max_instances` | int? | *unset* | 1–10 | Maximum instances |
| `scale_down_after_s` | int? | *unset* | 60–86400 | Scale-down idle time (seconds) |

`min_instances` description carries the always-on hint: *"Set to 1 to keep a warm worker and avoid ~5 min cold starts. 0 scales to zero when idle."* Explicitly **not** overloading `tasks_per_instance = 0` for this — a floor and a sensitivity are different knobs, and 0 breaks the division and existing validation.

**Nullable is required, not cosmetic.** The current service resolves an absent row to its hardcoded default (`settings.py:378`) and the UI displays that as *the* value — which would show `max_instances: 5` while the hand-tuned prod YAML says 3, and after "reset" the operator would read 5 while the autoscaler runs 3. Instead: `SettingDefinition` gains `nullable: bool = False`; for nullable definitions `default_value = None` means "inherit"; the settings GET for this namespace joins the latest tick snapshots and returns `effective_value` (and `override_error`) per key from the `policy` echo. The UI renders unset knobs as an empty field with placeholder **"Inherited from control plane: 3"**, set knobs as the explicit value, and "Reset to default" reads **"Inherit from control plane"**. If snapshots are stale, the effective value renders as "unknown (autoscaler not reporting)" rather than a guess. When the tick reports `override_error` (95.5), the tab shows a warning banner: *"Overrides rejected by the autoscaler and not in effect: \<detail\>"* — a discarded override must be visible where it was set, not only in an audit file.

**Unset is a first-class operation with delete semantics.** Today's update path upserts rows and validation rejects `None`, so "how does a field become unset again?" needs an explicit answer: `PATCH {"max_instances": null}` on a nullable definition **deletes that field's Postgres row** (and validation admits `null` for nullable keys only). A `{"v": null}` row must never be stored — an absent row and a null-valued row would be two distinct "unset" states with different resolution behavior. Namespace reset keeps its existing meaning (delete all rows) and thus reads "inherit everything". Covered by a round-trip test: set → value in row, hash, and tick echo; PATCH null → row gone, hash field gone (next sync), `effective_value` back to inherited.

**Namespace visibility.** The autoscale namespace is included in the settings namespace listing only when `dalston:autoscale:shapes` is non-empty — lite and non-autoscaled deployments must not see a tab full of "unknown" knobs that configure nothing.

**Mirror semantics — only explicit overrides propagate.** The Redis hash `dalston:autoscale:overrides` contains exactly the keys with a Postgres row; sync is an idempotent full rewrite of the hash from Postgres state. "Reset" removes the key → the YAML value applies.

**Failure semantics — Postgres is truth, Redis converges.** A PATCH commits to Postgres first, then best-effort syncs the hash; the write succeeds either way (the knob is durably set) and the response carries `redis_synced: bool`. A 60-second reconcile loop rewrites the hash from Postgres unconditionally, so a failed sync, a flushed Redis, or a Redis restart all heal within a minute — strictly stronger than startup-only healing, and idempotent so it needs no coordination. Loop mechanics: registered as an asyncio task in the gateway lifespan (`main.py`), started only in distributed mode (lite deployments have no autoscaler), cancelled and awaited on shutdown; each rewrite is atomic (`MULTI`: `DEL` + `HSET` + `EXEC`) so the tick never observes a half-written hash. The end-to-end confirmation signal remains `overrides_applied` in the tick echo: Settings shows a subtle "pending — not yet picked up by the autoscaler" hint until the echoed policy matches, which also covers the Postgres-committed-but-Redis-failed window honestly.

---

### 95.5: Tick applies overrides

**Files modified:**

- `infra/scripts/dalston-aws` — read overrides hash in `cmd_autoscale`, apply per shape
- `infra/scripts/dalston_autoscale.py` — `apply_overrides(policy, overrides) -> tuple[ShapePolicy, list[str], str | None]` (pure, unit-tested; returns effective policy, applied names, error)

**Deliverables:**

After `parse_policy`, each shape passes through `apply_overrides`, which builds a candidate `ShapePolicy` via the existing `from_dict` validation path. Rules:

- Only the four whitelisted keys are read; unknown hash fields ignored.
- Validation failure of the *combined* policy (e.g. override `min_instances=4` with YAML `max_instances=3`) → discard **all** overrides for that shape, audit `autoscale.bad_override`, run on pure YAML, **and publish the failure** as `policy.override_error` in the tick snapshot so Settings surfaces it (95.4). Never `die()`, never partially apply — a bad console value must not stop scaling or produce a half-applied policy, and must not vanish into a control-plane log file.
- Applied override names flow into the snapshot's `policy.overrides_applied`, so Settings changes are observable end-to-end within one tick.

Overrides are global (one hash, all shapes). With a single shape in production this is the right simplicity; per-shape overrides are a non-goal until a second shape exists.

---

### 95.6: Opt-in on-demand fallback

**Files modified:**

- `infra/scripts/dalston_autoscale.py` — `ShapePolicy.fallback_to_on_demand: bool = False`, `max_on_demand: int = 1`; fallback condition
- `infra/scripts/dalston-aws` — launch with `use_spot=False` on fallback; scale-down prefers on-demand workers first
- `infra/templates/autoscale-policy.yaml` — documented, default `false`

**Deliverables:**

When **all** hold — `fallback_to_on_demand` true, `blocked.consecutive_ticks >= 5` (≈5 min of spot failures), `desired > total`, and `on_demand_total < max_on_demand` — the tick launches one on-demand instance via the existing `launch_gpu_worker` path with `use_spot=False`.

**`on_demand_total` counts live *and* booting.** `discover_gpu_workers` returns both `pending` and `running` instances with their lifecycle (`dalston-aws:2912-2917,2928`), so `on_demand_total = on_demand_live + on_demand_pending`. Guarding on live alone would double-launch paid capacity: a fallback instance launched last tick is still `pending` this tick and must already count against the cap. Unit test required: an existing pending on-demand worker prevents a second fallback launch.

**No new lifecycle plumbing.** Fleet discovery already tags every instance with `spot: bool` from EC2's `InstanceLifecycle` (`dalston-aws:2928`); that flag drives the `spot_live`/`on_demand_live` snapshot counts (95.1), the fallback guard above, the scale-down preference (terminate on-demand before spot — they cost ~3×), and the strip label (95.3). No registry-record, node-identity, or `NodeView` changes — the console learns about on-demand workers from the snapshot counts, not from per-node fields.

Default **off**: this is a cost decision. YAML-only flag initially (not in Settings) — enabling paid fallback should be a deliberate control-plane edit, and the account has exactly 1 on-demand G-family quota. Ships last and is independently deployable; steps 95.1–95.5 are complete without it.

---

## Non-Goals

- **Console editing of `gpu_type_preference`, `boot_timeout_s`, `drain_wait_s`** — sharp-edged ops knobs with failure modes (reaping healthy boots) that don't belong next to product settings; YAML only.
- **Per-shape settings** — one shape exists; per-shape UI is speculative complexity.
- **Gateway writing the policy YAML** — crosses the control-plane trust boundary and breaks when the CP is unreachable; Redis overrides are the only channel.
- **Displaying the AWS spot quota next to `max_instances`** — needs Service Quotas API calls from the tick or gateway; worthwhile, separate slice.
- **Historical scaling charts** — Grafana already has the infra dashboards; the console strip is current-state only.
- **Consuming `autoscaler.*` pub/sub events** — the fire-and-forget events remain unconsumed; the tick snapshot key supersedes them for UI purposes.
- **Full-size ghost node cards** — rejected by design (see 95.3); slots carry the same information without the visual weight.
- **Exact job-failure prediction** — `earliest_wait_deadline_at` covers tasks already enqueued with deadlines; tasks not yet dispatched or without `waiting_for_engine` metadata aren't predicted, and the copy says "may fail", not "will fail".

---

## Deployment

Ordering constraint: ship the tick changes (95.1, 95.5) to the control plane **before** enabling the Settings namespace for operators (95.4 UI). If Settings lands first, knob changes write the overrides hash but no-op until the autoscaler reads it — the "pending — not yet picked up" hint makes this visible rather than silent, but the tab still shouldn't be user-visible before the CP is updated. Control-plane refresh follows the standard path: `dalston-aws launch control-plane` reconciliation or SSH pull + `systemctl restart dalston-autoscale.timer`.

No migration; new snapshot/blocked keys are TTL'd, the shapes marker is persistent but tiny, and the settings namespace is additive.

---

## Verification

```bash
# 1. Tick snapshot exists and echoes policy (on control plane, or via tailscale redis)
redis-cli GET "dalston:autoscale:tick:nemo+pyannote" | jq '.policy, .blocked, .applied, .spot_live'

# 2. Shapes marker persists independently of snapshots
redis-cli HGETALL "dalston:autoscale:shapes"

# 3. Blocked counter accumulates across ticks WITH backlog present
#    (guards the _update_idle_state hash-delete regression)
redis-cli HGETALL "dalston:autoscale:blocked:nemo+pyannote"   # ticks must increase, not reset

# 4. Gateway surfaces it, including stale flag and wait deadline
curl -s http://localhost:8000/api/console/nodes -H "Authorization: Bearer $DALSTON_API_KEY" \
  | jq '.autoscaler[] | {shape, stale, last_tick_at, desired, live, blocked, earliest_wait_deadline_at}'

# 5. Override round-trip: set tasks_per_instance=10, confirm hash + next tick echo
curl -s -X PATCH http://localhost:8000/api/console/settings/autoscale \
  -H "Authorization: Bearer $DALSTON_API_KEY" -H "Content-Type: application/json" \
  -d '{"tasks_per_instance": 10}'
redis-cli HGETALL "dalston:autoscale:overrides"          # → tasks_per_instance 10 (only)
sleep 70
redis-cli GET "dalston:autoscale:tick:nemo+pyannote" | jq '.policy'
# → tasks_per_instance: 10, overrides_applied: ["tasks_per_instance"]

# 5b. Unset via null deletes the row and the hash field (round-trip)
curl -s -X PATCH http://localhost:8000/api/console/settings/autoscale \
  -H "Authorization: Bearer $DALSTON_API_KEY" -H "Content-Type: application/json" \
  -d '{"tasks_per_instance": null}'
redis-cli HGETALL "dalston:autoscale:overrides"          # → empty (after next sync)

# 6. Inheritance is truthful: unset knob shows the YAML value, not the code default
curl -s http://localhost:8000/api/console/settings/autoscale -H "Authorization: Bearer $DALSTON_API_KEY" \
  | jq '.settings[] | {key, value, effective_value}'     # unset → value null, effective from tick

# 7. Reconcile heals a flushed Redis within a minute
redis-cli DEL "dalston:autoscale:overrides"; sleep 70
redis-cli HGETALL "dalston:autoscale:overrides"          # → restored from Postgres

# 8. Bad override never stops scaling and is surfaced (unit + snapshot)
pytest infra/scripts/test_dalston_autoscale.py -k override
redis-cli GET "dalston:autoscale:tick:nemo+pyannote" | jq '.policy.override_error'

# 9. UI: kill the timer and confirm the stale strip appears (marker persists, snapshot ages)
ssh <control-plane> sudo systemctl stop dalston-autoscale.timer   # strip → "not reporting" within ~3 min
```

---

## Checkpoint

- [ ] Tick writes `dalston:autoscale:tick:<shape>` (TTL 3600s) with `schema_version`, policy echo, `override_error`, and `spot_live`/`on_demand_live`
- [ ] `dalston:autoscale:shapes` marker rewritten atomically from the current policy every tick; a shape removed from YAML disappears within one tick; deprovision deletes marker + all autoscale keys
- [ ] Blocked state in dedicated `dalston:autoscale:blocked:<shape>` key; counter proven to accumulate across ticks while backlog exists
- [ ] `/api/console/nodes` returns `autoscaler[]` with `stale`/`last_tick_at`; a corrupt or unknown-`schema_version` snapshot degrades that shape to stale, never the whole response
- [ ] `earliest_wait_deadline_at` derived from `SMEMBERS dalston:waiting_engine_tasks` + pipelined `HMGET dalston:task:<id> engine_id wait_deadline_at`, filtered to the shape's engines
- [ ] Infra strip renders slot row, formula line, status line; idle/cooldown/warning/critical/stale states all reachable; critical uses the task deadline when available and blocked-duration otherwise — never a fabricated countdown
- [ ] Settings → Autoscale: 4 nullable knobs (`nullable` on `SettingDefinition`); unset renders "Inherited from control plane: N" from the tick echo, "unknown" when stale; namespace hidden when the shapes marker is empty
- [ ] `PATCH {key: null}` deletes the row (never stores `{"v": null}`); round-trip test: set → visible in row/hash/echo, null → row and hash field gone, effective value back to inherited
- [ ] Only explicit values mirrored to `dalston:autoscale:overrides`; PATCH commits Postgres first and reports `redis_synced`; 60s reconcile loop (lifespan-managed, distributed mode only, atomic `MULTI` rewrite, cancelled on shutdown) heals a flushed/failed Redis
- [ ] `apply_overrides` unit-tested: valid override applied, invalid combination fully discarded + audited + published as `override_error`; Settings shows the rejection banner
- [ ] `overrides_applied` visible in tick snapshot within one tick of a Settings change; Settings shows "pending" until the echo matches
- [ ] On-demand fallback (default off): guard uses `on_demand_total = live + pending` from the existing EC2 `spot` flag (no registry changes); unit test proves a pending on-demand worker blocks a second launch; labeled in UI, terminated first at scale-down
