# M91: Queue-Based GPU Autoscaling (Scale-to-Zero Spot Workers)

|                    |                                                              |
| ------------------ | ------------------------------------------------------------ |
| **Goal**           | GPU spot workers launch themselves when episodes are queued and terminate when the queue stays empty, with jobs accepted even when no GPU engine is running — an always-on control plane and a GPU bill that tracks actual transcription volume |
| **Duration**       | 5–8 days                                                     |
| **Dependencies**   | M80 (Engine Control Plane), M64 (Registry Unification), M87 (Queue Board) |
| **Deliverable**    | `dalston-aws autoscale` control loop (systemd timer on control plane), non-interactive spot launch with GPU-type fallback, tag-based stateless fleet discovery, GPU-worker dead-man switch, on-demand engine acceptance in `engine_selector`, stale-task watchdog, dry-run mode, phased ops runbook |
| **Status**         | Implemented — pending live dry-run validation                |

## User Story

> *"As the operator of thestill, I want the always-on control plane to accept episodes around the clock and provision GPU spot workers only while there is work in the queue, so that the service scales from 20 to 500 episodes/day by changing config numbers — not architecture — and never bills for idle GPUs."*

---

## Workload Assumptions (thestill production)

These numbers drive every threshold below; revisit them if the product changes.

| Parameter | Value | Source |
| --------- | ----- | ------ |
| Episode volume | 20–500 episodes/day (~20–500 audio-hours/day) | product estimate |
| GPU cost per audio-hour | ~10 GPU-minutes (measured on g4dn/T4) | benchmark |
| Daily GPU demand | **3.3 GPU-h/day (low) → ~83 GPU-h/day (high)** | derived |
| Instances needed at peak | 83 / 24 ≈ **3.5 sustained** → cap at 5 | derived |
| Worker shape | one EC2 instance co-locating `nemo` + `pyannote` containers | current launch practice |
| Cold start | 3–8 min (spot launch + NVMe model download) | observed |
| g6.xlarge eu-west-2 | $1.0216/hr on-demand; spot typically 30–40% of that | AWS pricing API 2026-07 |

Two consequences reviewers should hold in mind:

1. **Parallelism is latency, not cost.** 83 GPU-hours costs the same on 1 instance or 5. The only cost lever is *idle elimination* (scale-to-zero); `max_instances` is chosen from the publishing-latency promise, not from spend.
2. **The autoscaler must be correct in both regimes.** At 500 eps/day the queue never drains and the loop simply holds 4–5 instances up (utilization ≈ 100%, autoscaling saves little). At 20 eps/day it saves 4–7× by scaling to zero between arrivals. Same loop, different config numbers.

---

## Outcomes

| Scenario                                       | Current                                                          | After M91                                                                                  |
| ---------------------------------------------- | ---------------------------------------------------------------- | ------------------------------------------------------------------------------------------ |
| Episode arrives with zero GPU workers up       | Job **rejected** (`NoCapableEngineError`) — no live registry record for nemo/pyannote | Job accepted, tasks enqueued; autoscaler launches a worker; transcript ready ~5–15 min later |
| Queue drains and stays empty for the cooldown  | GPU spot instances bill until operator runs `terminate gpu`      | Workers drain in-flight tasks, then self-terminate; GPU spend goes to $0                   |
| Backlog exceeds one instance's threshold       | Operator manually launches more workers                          | Loop launches up to `max_instances` (ceil(backlog/20)), scales back down as queue depletes |
| Spot instance interrupted mid-backlog          | Reconciler re-queues tasks after 10 min; nothing relaunches      | Worker drops from registry (60s TTL); persistent backlog relaunches within one tick        |
| Control plane stopped/crashed with workers up  | Orphaned spots bill until noticed                                | Each worker's dead-man switch self-terminates after ~10 min without Redis contact          |
| Operator's laptop is closed                    | Laptop `dalston-aws` is the only actuator                        | Laptop only starts/stops the control plane; all GPU scaling happens on AWS via the control plane's IAM role |

---

## Design Decisions (fixed for this milestone)

1. **Control plane stays always-on** (t3.large, ~$60/mo). It is the queue, the scale signal, and the actuator host. Only GPU workers scale.
2. **The autoscaler lives on the control plane**, as a `dalston-aws autoscale --once` subcommand fired by a systemd timer (~every 60s). No daemon, no new `dalston/` package, no laptop involvement. `dalston-aws` already owns launch/terminate, spot-AZ selection, VRAM `coloc_with_*` budgets, and the audit log — the loop reuses them.
3. **Fleet state is stateless.** GPU workers are discovered via EC2 tags (`dalston:role=gpu-worker`, `dalston:shape=nemo+pyannote`) + the Redis registry — never via `~/.dalston/aws-state.yaml`, which lives on the laptop and would go permanently stale the moment the control plane launches a worker autonomously. The state file remains authoritative only for the control plane itself (VPC, IAM, keys). Laptop `status`/`terminate gpu` read the same tags, so manual and automatic actions coexist without coordination.
4. **The scaling unit is the worker shape**, not the engine: one EC2 instance = the `nemo`+`pyannote` bundle, launched and terminated atomically. Demand is the **max** of the shape's per-engine backlogs (one instance serves both engines simultaneously); scale-down requires **every** engine in the shape idle.
5. **Scale-to-zero requires lazy acceptance.** Episodes arrive unattended into a possibly-empty fleet, so the "no live engine → reject" check must fall back to the engine catalog for engines flagged on-demand. This is the only change to Dalston core, and it is in scope (91.5) — without it the whole design fails at its first idle-arrival.
6. **Spot everywhere, with diversified fallback.** At peak (~4 instances × 20h/day) interruptions are routine, not exceptional. The launch path must walk an ordered GPU-type/AZ list (g6 → g4dn → g5) non-interactively; correctness is already guaranteed by the reconciler re-queue + backlog-driven relaunch.
7. **Cold start of minutes is accepted.** No warm pools, no AMI-baked models (Non-Goals). Boot waste is pennies/day at every projected volume.

---

## Architecture

```
                    laptop: dalston-aws up/down, launch --autoscale  (only manual act)
                                        │
┌───────────────────────────── CONTROL PLANE (always-on EC2) ─────────────────────────────┐
│                                                                                          │
│  Gateway ─▶ Orchestrator ─▶ Redis Streams          Unified Registry (Redis)              │
│     │        (91.5: on-demand   dalston:stream:{eid}   dalston:engine:instance:*         │
│     │         catalog fallback)      │                        │                          │
│     │        (91.6: stale-task      │ lag + PEL              │ heartbeat 10s / TTL 60s  │
│     │         watchdog)             ▼                        ▼                          │
│     │            ┌──────────────────────────────────────────────────┐                   │
│     │            │   dalston-aws autoscale --once  (systemd timer)   │                   │
│     │            │   per shape:                                      │                   │
│     │            │     backlog_e = lag_e + PEL_e                     │                   │
│     │            │     desired = clamp(ceil(max_e backlog_e / 20),   │                   │
│     │            │                     0, max_instances)             │                   │
│     │            │     current = EC2 tag scan ∩ registry node_ids    │                   │
│     │            │     single-flight launch / drain-then-terminate   │                   │
│     │            └───────────────┬───────────────┬─────────────────┘                    │
│     │                     launch │               │ drain + terminate                    │
│     │                            ▼               ▼                                       │
│     │              boto3 RunInstances / TerminateInstances  (control-plane IAM role)     │
└─────┼────────────────────────────┼──────────────────────────────────────────────────────┘
      │                            ▼
      │        ┌────────────────────────────────────────┐   × up to max_instances
      │        │ GPU spot worker (tagged dalston:role=…) │
      │        │  nemo + pyannote containers, one GPU    │
      │        │  dead-man switch: no Redis contact      │
      │        │  for 10 min → shutdown -h now           │◀── covers CP stop/crash,
      │        └────────────────────────────────────────┘    Tailscale loss, orphaning
      │
      └── episodes accepted 24/7, even at fleet size 0
```

Existing primitives reused (no new infrastructure):

- **Backlog** — lag (`XINFO GROUPS`) + PEL (`XPENDING`) per `dalston:stream:{engine_id}`; already surfaced at [dalston/metrics.py:378](../../dalston/metrics.py#L378) and [engine_sdk/runner.py:376](../../dalston/engine_sdk/runner.py#L376).
- **Liveness** — registry records grouped by `node_id` ([dalston/common/registry.py](../../dalston/common/registry.py)); co-located containers collapse to one instance.
- **Actuator** — `dalston-aws` multi-engine GPU launch (Tailscale join, NVMe prep, VRAM budgets, spot-AZ pricing) and terminate.
- **Failure recovery** — orchestrator reconciler re-queues orphaned tasks ([dalston/orchestrator/reconciler.py](../../dalston/orchestrator/reconciler.py)).

---

## Steps

### 91.1: Non-interactive launch/terminate + tag-based fleet tracking

**Files modified:**

- `infra/scripts/dalston-aws` — factor GPU launch/terminate into functions callable without a TTY; replace the interactive spot-capacity prompt ([dalston-aws:2320](../../infra/scripts/dalston-aws#L2320)); tag workers at launch; make `stop`/`terminate control-plane` terminate all tagged GPU workers first

**Deliverables:**

```python
class SpotCapacityError(RuntimeError):
    """No configured GPU type/AZ has spot capacity right now."""

def launch_gpu_worker(
    engines: list[str],                      # the shape, e.g. ["nemo", "pyannote"]
    *,
    gpu_type_preference: list[str],          # ordered fallback, e.g. ["g6.xlarge", "g4dn.xlarge", "g5.xlarge"]
    non_interactive: bool = False,
) -> GpuWorker:
    """One EC2 instance running the whole shape (reuses the existing multi-engine
    path so coloc_with_* VRAM budgets resolve). Tags the instance:
      dalston:role=gpu-worker, dalston:shape=<sorted engine list>,
      dalston:managed-by=autoscaler|manual
    In non_interactive mode walks gpu_type_preference across AZs, then raises
    SpotCapacityError (caller backs off; next timer tick retries)."""

def terminate_gpu_worker(instance_id: str) -> None:
    """Cancel spot request, terminate the instance (whole shape dies together)."""

def discover_gpu_workers() -> list[GpuWorker]:
    """EC2 DescribeInstances filtered on dalston:role=gpu-worker tag, running/pending.
    The single source of truth for the fleet — replaces state-file tracking for
    GPU workers. Laptop `status` / `terminate gpu` use this too."""
```

Ships independently: manual `dalston-aws launch gpu` gains tagging and the fallback list; nothing else changes.

---

### 91.2: Dead-man switch on GPU workers

**Files modified:**

- `infra/scripts/dalston-aws` — GPU user-data (`generate_gpu_user_data()` region): install a systemd timer on the worker

**Deliverables:**

A one-minute timer on each GPU worker that pings control-plane Redis over Tailscale; after 10 consecutive failures it runs `shutdown -h now` (terminates a spot instance). ~15 lines of shell. Covers: control plane stopped or crashed, deliberate `dalston-aws down`, Tailscale/auth-key failure, any orphaning. Ships independently and is valuable even without the autoscaler.

---

### 91.3: Autoscale signals + policy + loop (`dalston-aws autoscale`)

**Files modified:**

- `infra/scripts/dalston-aws` — new `autoscale` subcommand (`--once`, `--dry-run`); policy config

**Deliverables:**

Per tick (all pure reads until the final action):

1. **Backlog** per engine_id in the shape: `lag + PEL` from Redis.
2. **Current fleet**: `discover_gpu_workers()` (tags) cross-checked against registry `node_id`s. An EC2 instance whose containers haven't all registered yet counts as *pending*, not live — this is the single-flight guard (never launch #2 while #1 boots).
3. **Desired**: `clamp(ceil(max_e backlog_e / tasks_per_instance), min_instances, max_instances)`.
4. **Act**: launch one, or drain-and-terminate one, or nothing. One action per tick per shape (the 60s timer provides natural debounce).

```yaml
# policy config (control plane, provisioned by --autoscale)
shapes:
  - engines: [nemo, pyannote]
    stream_engine_ids: [nemo, pyannote-4.0]
    tasks_per_instance: 20        # → 1 instance ≤20 queued, 2 ≤40, … capped
    min_instances: 0              # scale-to-zero
    max_instances: 5              # sized for 500 eps/day sustained (~3.5) + burst headroom
    scale_down_after_s: 2100      # 35 min: ALL engines lag==0 AND PEL==0 this long
    drain_wait_s: 60              # bounded PEL wait before terminate gives up this tick
    boot_timeout_s: 1800          # reap a running-but-never-registered worker (wedged boot)
    gpu_type_preference: [g4dn.xlarge, g6.xlarge, g5.xlarge]  # operator AZ availability
```

Scale-down rules (the part that prevents wasted work):

- Only when **every** engine in the shape has `lag == 0 AND in_flight == 0` sustained for `scale_down_after_s`. Checking PEL is what stops terminating a worker mid-episode.
- Before terminating: set `draining` on all the instance's registry records, wait (bounded) for PEL to empty, then terminate. The reconciler remains the backstop if drain times out.
- The 35-min cooldown is tuned for trickle arrivals (20 eps/day ≈ one every ~70 min): it trades ~$2–4/day of idle at low volume for avoiding boot-thrash and repeated cold-start latency. It is the first knob to re-tune from real arrival data.
- On `SpotCapacityError`: log + audit, do nothing; next tick retries down the fallback list.
- Every action → `dalston-aws` audit log + registry event (`autoscaler.scale_up` / `autoscaler.scale_down`).

`--dry-run` prints the full decision (per-engine backlog, live/pending fleet, desired, action) as JSON and takes no action.

---

### 91.4: Provision the loop on the control plane

**Files modified:**

- `infra/scripts/dalston-aws` — `--autoscale` flag on setup/start: control-plane user-data installs the script + policy config + systemd timer (60s); extend [create_iam_role()](../../infra/scripts/dalston-aws#L602) with `ec2:RunInstances`, `ec2:TerminateInstances`, `ec2:DescribeInstances`, `ec2:DescribeSpotPriceHistory`, `ec2:CreateTags`, `ec2:CancelSpotInstanceRequests`, `iam:PassRole`

**Deliverables:**

`dalston-aws launch control-plane --autoscale` (or `autoscale --provision` on a running control plane) → control plane runs with the timer enabled; the laptop's only remaining role is starting/stopping the control plane. Default **off**: a control plane started without the flag behaves exactly as today. Rollback = disable the timer; manual commands unaffected.

---

### 91.5: Lazy acceptance — on-demand engines in `engine_selector`

**Files modified:**

- `dalston/orchestrator/engine_selector.py` — catalog fallback at the two rejection sites ([engine_selector.py:575](../../dalston/orchestrator/engine_selector.py#L575), [engine_selector.py:626](../../dalston/orchestrator/engine_selector.py#L626))
- engine catalog metadata — `on_demand: true` flag for nemo / pyannote

**Deliverables:**

When no live registry record matches the stage but the catalog has a capable engine flagged `on_demand`, select it from **catalog capabilities** and enqueue the task instead of raising `NoCapableEngineError`. Redis streams accept messages without a consumer, so no grace-period timer is needed — the task sits on `dalston:stream:{engine_id}`, the backlog triggers a launch, and the worker consumes it on boot. Engines not flagged `on_demand` keep today's fail-fast behaviour.

---

### 91.6: Stale-task watchdog

**Files modified:**

- `dalston/orchestrator/reconciler.py` (or sibling) — fail jobs whose tasks were never delivered

**Deliverables:**

Safety net for the lazy-accept path: if a task sits **undelivered** (stream lag, never claimed) beyond `DALSTON_TASK_STALE_TIMEOUT_S` (default 1800s — several autoscaler ticks + boot + headroom), fail the job with an explicit "no worker became available" error. Catches: autoscaler disabled/broken, spot capacity exhausted across the whole fallback list, misconfigured shape. Distinct from the existing reconciler path, which handles *claimed-then-orphaned* tasks.

---

## Cost Model

| Regime | GPU demand | Fleet behaviour | GPU cost (spot ≈ $0.30–0.40/hr) |
| ------ | ---------- | --------------- | ------------------------------- |
| 2 users / early days | <1 GPU-h/day | 0 instances almost always | **~$0.50–2/day** |
| 20 eps/day | ~3.3 GPU-h/day | 1 instance, up in bursts + cooldown | ~$3–6/day |
| 500 eps/day | ~83 GPU-h/day | 4–5 instances near-24/7 (queue never drains) | ~$25–35/day (≈ the work itself; autoscaling saves little here by design) |
| Rejected: always-on 1× on-demand g6.xlarge | — | — | $24.50/day ($746/mo), ~95% idle at low volume |

Plus the always-on control plane (~$60/mo). Cold-start waste (boots × ~5 min) is <$0.50/day at every projected volume — AMI baking is deliberately deferred.

---

## Phased Ops Runbook (scale by config, not architecture)

| Observed trigger | Change | Effort |
| ---------------- | ------ | ------ |
| Backlog regularly needs >1 instance | raise `max_instances` 1 → 3 → 5 | one line |
| Boot-thrash on trickle arrivals | `scale_down_after_s` 2100 → 2700+ | one line |
| Spot capacity misses stall the queue | broaden `gpu_type_preference` / AZs | config |
| Sustained >50 GPU-h/day | benchmark $/audio-hour g6 vs g4dn, reorder preference (L4 is typically 1.5–2× T4 at similar spot price) | 1-day test |
| Frequent boots despite cooldown tuning | AMI-bake models (boot ~5 min → ~2 min) | separate milestone |
| Double-digit fleet / multi-region | reconsider ASG (see Non-Goals) | separate milestone |

---

## Non-Goals

- **AWS-native ASG + CloudWatch** — evaluated and rejected at this scale: the co-located shape needs a composite metric the control plane must compute anyway; the bespoke provisioning (Tailscale, NVMe, VRAM budgets) would be duplicated into launch templates and maintained twice; scale-in protection still requires the same PEL-aware drain logic. Revisit at double-digit fleet or multi-region.
- **Sleeping the control plane** — it is the queue and must accept episodes 24/7.
- **Warm pools / AMI-baked models** — minutes-scale cold start accepted; baking is a follow-up if boot churn is observed.
- **Predictive or schedule-based scaling** — reactive only; a cron warm-up is trivial to add later if arrival patterns prove periodic.
- **Bin-packing / overlapping shapes** — each engine belongs to exactly one shape, matching how workers are launched today.
- **Autoscaling CPU engines** — they run on the control plane; no GPU spend to reclaim.
- **Replacing the reconciler** — it remains the correctness backstop for orphaned tasks.

---

## Deployment

1. **91.1 + 91.2 first** (tagging, non-interactive launch, dead-man switch) — pure improvements to the manual workflow; no behaviour change otherwise. The dead-man switch alone ends orphaned-spot billing.
2. **91.3 in `--dry-run`** on the live control plane: watch decisions against real traffic for a few days; verify launch/terminate decisions match what you would have done by hand.
3. **91.4** enable actuation (`--autoscale`), initially with `max_instances: 1`.
4. **91.5 + 91.6** land lazy-accept + watchdog; from this point the fleet may legitimately be at zero while jobs arrive.
5. Raise `max_instances` per the runbook as volume grows.

IAM permission additions (91.4) are additive; rollback at any stage = disable the systemd timer.

---

## Verification

```bash
# Dry-run against control-plane Redis: full decision as JSON, no actions
dalston-aws autoscale --once --dry-run | jq '.'
# Expect per shape: {engines, per_engine:{lag,in_flight}, live, pending, desired, action}

# Lazy accept: with ZERO GPU workers running, submit a job requesting nemo
curl -s -X POST http://<cp>:8000/v1/audio/transcriptions ... | jq '.status'
# Expect: job accepted (queued), NOT NoCapableEngineError

# Backlog triggers launch (live mode): tasks appear on both streams, one instance launches
redis-cli XINFO GROUPS dalston:stream:nemo
dalston-aws autoscale --once | jq '.[].action'       # "launch" (output is a per-shape array)
aws ec2 describe-instances --filters "Name=tag:dalston:role,Values=gpu-worker" \
  --query 'Reservations[].Instances[].State.Name'    # one "pending"/"running"

# Scale-down: queue empty (lag+PEL==0 on BOTH streams) for cooldown → drain + terminate
tail -f ~/.dalston/audit.log                          # autoscale.terminate entry

# Dead-man switch: sever Redis contact with a worker still running.
# NOTE: `dalston-aws down` can't test this — it terminates autoscaler workers
# itself before stopping the control plane. Stop the CP out-of-band instead:
aws ec2 stop-instances --instance-ids <control-plane-id>
# (or on the CP: docker compose stop redis)
# Expect: worker terminates itself within ~10 min (check EC2 console / describe-instances)

# Watchdog: disable the timer, submit a job, wait DALSTON_TASK_STALE_TIMEOUT_S
# Expect: job fails with explicit "no worker became available" error, not silent hang
```

---

## Checkpoint

- [ ] `launch_gpu_worker` / `terminate_gpu_worker` / `discover_gpu_workers` callable non-interactively; workers tagged; spot fallback walks `gpu_type_preference` and raises `SpotCapacityError` when exhausted
- [ ] `terminate control-plane` terminates **all** tagged GPU workers first; `dalston-aws down` terminates autoscaler-managed workers and stops manual ones (a stopped on-demand manual worker stays resumable via `up`)
- [ ] Dead-man switch: worker self-terminates after ~10 min without Redis contact
- [ ] `autoscale --once`: desired = `clamp(ceil(max backlog / 20), 0, max)`; single-flight (pending instances counted); scale-down only when the whole shape has `lag==0 && PEL==0` for the cooldown; drains all co-located engines before terminate; every action audited
- [ ] Fleet state derived from EC2 tags + registry only — `aws-state.yaml` no longer tracks GPU workers; laptop `status`/`terminate gpu` read tags
- [ ] `--dry-run` prints decisions without acting; `--autoscale` provisioning default-off with IAM additions documented
- [ ] `engine_selector` accepts jobs for `on_demand`-flagged engines with zero live instances (catalog fallback); non-flagged engines unchanged
- [ ] Stale-task watchdog fails never-delivered jobs after the timeout with an explicit error
- [ ] End-to-end: zero fleet → episode submitted → accepted → instance auto-launches → transcript delivered → fleet returns to zero after cooldown
