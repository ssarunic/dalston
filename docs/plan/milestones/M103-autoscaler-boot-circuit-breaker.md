# M103: Autoscaler Boot Circuit Breaker

|                    |                                                              |
| ------------------ | ------------------------------------------------------------ |
| **Goal**           | Stop the GPU autoscaler from relaunching into a boot failure every tick; back off exponentially and make the failure visible |
| **Duration**       | 1 day                                                        |
| **Dependencies**   | M91 (queue-based autoscaling), M95 (autoscaler console visibility) |
| **Deliverable**    | Per-shape boot-failure streak in Redis, exponential launch backoff in `decide()`, `boot_failure` blocked kind on the console strip, `--reset-boot-failures`, reaper drops the shadow card |
| **Status**         | In Progress                                                  |

## User Story

> *"As the operator, I want a worker that keeps failing to boot to stop the autoscaler from relaunching it every 30 minutes, so that a broken boot path costs a couple of launches and a red strip on the console instead of three days of silent spot churn."*

---

## Motivation

On 2026-09-12 the Tailscale auth key in SSM hit its 90-day limit. Every GPU worker launched after that aborted its user-data at `tailscale up`, never registered, and was reaped by the stuck-boot timeout after exactly 30 minutes. The backlog was still there, so the next tick launched again. This ran for three days: ~70 launches, ~$7 of spot, a job that failed on its on-demand wait, and no alert. The autoscaler was behaving exactly as designed; the design had no memory of the previous failure.

The stuck-boot reaper (M91) exists so one wedged boot cannot hold the single-flight slot forever. It is the right tool for one failure and the wrong tool for a repeating one: a reap is evidence that the boot path is broken, and the only thing that changes between reaps is the instance id. The same loop happens for any cause that breaks user-data or engine startup: an expired join credential, a bad `:latest` image, a broken new AMI, a model download that OOMs.

---

## Outcomes

| Scenario | Current | After M103 |
| -------- | ------- | ---------- |
| Boot path broken, backlog present | launch, reap after `boot_timeout_s`, relaunch, forever (~48 launches/day) | 2 launches, then backoff doubling from `boot_timeout_s` to a 6 h cap (~6 launches/day) |
| Console during the loop | amber "booting" card every 30 min, otherwise nominal strip | red strip: *N consecutive boot failures — launches paused* with the reset command |
| Operator fixes the cause | wait for the next tick | `dalston-aws autoscale --reset-boot-failures` on the control plane, or just let a worker boot live |
| One transient failure (nvme-prep race, spot reclaim mid-boot) | reap + relaunch | same — the first failure is tolerated, the breaker trips on the second |
| Old worker healthy, new launches broken (bad new AMI) | loop continues while the old worker serves | breaker trips: only a worker launched *after* the last failure closes it |
| Reaped worker's shadow card | lingers up to 180 s as "Booting · stuck" | deleted by the reaper |

---

## Architecture

```
┌──────────────────────────── autoscale tick (per shape) ─────────────────────────────┐
│                                                                                     │
│  read bootfail:<shape> ──▶ recovered? (a LIVE worker launched after last_failure)   │
│        │                        │ yes: DEL key, audit boot_circuit_closed           │
│        ▼                        ▼                                                   │
│  decide(..., boot_failures=N, boot_backoff_remaining_s=R)                           │
│        │  desired > total & no pending & R > 0  ──▶ NONE "boot circuit open"        │
│        ▼                                                                            │
│  _reap_stuck_pending ── terminate ──▶ DEL pending:<iid>                             │
│                                   └─▶ HSET bootfail:<shape> count+1 (dedupe iid)    │
│                                        count == 2 ──▶ audit + publish               │
│                                                       autoscaler.boot_circuit_open  │
│        ▼                                                                            │
│  snapshot.blocked = boot_failure (if R > 0) else spot streak                        │
│  snapshot.boot_failures = {count, since, last_instance_id, last_failure_at, ids}    │
└─────────────────────────────────────────────────────────────────────────────────────┘

backoff(count) = 0                                   count < 2
               = min(6h, boot_timeout_s · 2^(count-2))   otherwise
               → with boot_timeout_s=1800: 30m, 1h, 2h, 4h, 6h, 6h, …
```

The counter lives in `dalston:autoscale:bootfail:<shape>` (dry-run: `bootfail:dryrun:<shape>`), **not** in `blocked:<shape>`: the blocked tick streak drives the M95.6 on-demand fallback, and paid capacity does not fix a boot that never registers. The `boot_failure` kind is synthesised into the tick snapshot's `blocked` field only, so the existing warning/critical strip states render it with no gateway change.

---

## Steps

### 103.1: Pure decision logic

**Files modified:**

- `infra/scripts/dalston_autoscale.py` — `BootFailureInfo`, `boot_failure_backoff_s`, `boot_backoff_remaining_s`, `boot_failures_recovered`; `decide()` gains `boot_failures` / `boot_backoff_remaining_s`; `BlockedInfo.kind` accepts `boot_failure`; snapshot gains `boot_failures`
- `tests/unit/test_dalston_autoscale.py`

**Deliverables:**

```python
BOOT_FAILURE_TRIP_THRESHOLD = 2
BOOT_FAILURE_BACKOFF_CAP_S = 6 * 3600

def decide(..., boot_failures: int = 0, boot_backoff_remaining_s: float = 0.0) -> ScaleDecision
```

The breaker is checked after single-flight and before the on-demand fallback. It only ever suppresses a launch; scale-down and drain are untouched.

---

### 103.2: Redis state, reaper hook, tick integration, reset

**Files modified:**

- `infra/scripts/dalston-aws` — `_read_boot_failures` / `_record_boot_failure` / `_clear_boot_failures`, reaper records the failure and deletes the pending shadow key, `_snapshot_blocked`, recovery check in the tick, `autoscale --reset-boot-failures`, deprovision cleanup of `bootfail:*`
- `tests/unit/test_dalston_aws_autoscale.py`

**Deliverables:**

- `_record_boot_failure` is idempotent per instance id across the whole streak: the hash keeps every counted id in `instance_ids`, so dry-run re-reporting the same stuck workers each tick neither inflates the count nor restarts the backoff.
- Audit events `autoscale.boot_circuit_open`, `autoscale.boot_circuit_closed`, `autoscale.boot_failures_reset`; registry pub/sub event `autoscaler.boot_circuit_open` for a future alert consumer.

---

### 103.3: Console wording

**Files modified:**

- `web/src/lib/strings.ts`, `web/src/components/AutoscalerStrip.tsx`, `web/src/api/types.ts`, `web/src/lib/autoscalerStrip.test.ts`

**Deliverables:**

`blocked.kind == "boot_failure"` renders *N consecutive boot failures — launches paused, backlog B. Check the worker console log, fix the cause, then run dalston-aws autoscale --reset-boot-failures.* The critical variant appends *Launches paused for X* instead of the spot wording.

---

## Non-Goals

- **Rotating or replacing the Tailscale credential** — the trigger this time, but the breaker is cause-agnostic. The OAuth-client join is a separate change (M93 already specifies a tagged ephemeral GPU join credential).
- **Alerting** — the `autoscaler.boot_circuit_open` event and audit line are published; wiring a Grafana rule is observability work.
- **Diagnosing the failure automatically** — the strip points at the instance console log; parsing it is not attempted.
- **Making the join non-fatal in user-data** — a `set -e` user-data that self-terminates on join failure would shorten each failed attempt from 30 min to ~2; worthwhile, separate.

---

## Verification

```bash
# Unit
.venv/bin/pytest tests/unit/test_dalston_autoscale.py tests/unit/test_dalston_aws_autoscale.py -q
cd web && npx vitest run src/lib/autoscalerStrip.test.ts

# On the control plane, after two reaps of a shape:
redis-cli HGETALL "dalston:autoscale:bootfail:nemo+pyannote"      # count=2
redis-cli GET "dalston:autoscale:tick:nemo+pyannote" | jq '.blocked, .boot_failures, .reason'
#   .blocked.kind == "boot_failure", .reason starts with "boot circuit open"
sudo journalctl -u dalston-autoscale.service | grep -E "boot_circuit_open|Boot circuit OPEN"

# Operator reset
sudo -u ec2-user /data/dalston/.venv/bin/python /data/dalston/infra/scripts/dalston-aws \
  autoscale --reset-boot-failures
redis-cli EXISTS "dalston:autoscale:bootfail:nemo+pyannote"       # 0
```

---

## Checkpoint

- [ ] Two consecutive reaps open the breaker; the third launch waits `boot_timeout_s`, the fourth `2 × boot_timeout_s`, capped at 6 h
- [ ] A live worker launched after the last failure closes the breaker; a worker live since before the streak does not
- [ ] Breaker suppresses on-demand fallback as well as spot launches
- [ ] Reaper deletes `dalston:autoscale:pending:<iid>` for the instance it terminates
- [ ] Tick snapshot carries `boot_failures` and, while open, `blocked.kind == "boot_failure"`
- [ ] Console strip renders the boot-failure wording in warning and critical states
- [ ] `autoscale --reset-boot-failures` clears live and dry-run keys for every shape; `--deprovision` removes `bootfail:*`
