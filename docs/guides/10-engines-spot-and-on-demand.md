# Engines on spot vs on-demand — the mental model

> Rent a GPU only while you need it. Spot prices are roughly **65–70% cheaper**
> than on-demand. A one-hour podcast on a g6.xlarge spot instance costs less
> than a coffee. When you're done, `engine down` and AWS stops charging.

This page explains **what** spot and on-demand actually mean inside Dalston,
when each is the right choice, and what happens when AWS reclaims a spot
instance. If you just want to launch an engine and try it, jump to
[11-single-engine-tailscale-mode.md](11-single-engine-tailscale-mode.md).

---

## The two pricing models, in one paragraph

**On-demand** is the default AWS instance you boot. You pay a fixed hourly
rate for as long as it runs. You can `stop` it (no compute charge, but EBS
still bills) or `terminate` it (gone forever).

**Spot** instances use AWS's spare capacity at a steep discount, but AWS can
take them back with **2 minutes' notice** when paying customers need that
capacity. In Dalston's setup, spot instances cannot be stopped — only
terminated. You restart by launching a new one. (Source:
[`infra/scripts/dalston-aws`](../../infra/scripts/dalston-aws), the spot
handling in `_stop_instance` raises `UnsupportedOperation` and falls through
to terminate.)

---

## When to pick which

| You're doing this | Pick this | Why |
|---|---|---|
| One-off batch run (transcribe a back catalog) | **spot** | Job restarts on its own if reclaimed; you save 65% |
| 24/7 ElevenLabs/OpenAI-compatible API | **on-demand** for the control plane, **spot** for GPU workers | Control plane needs to stay up to accept requests; GPU workers can be ephemeral |
| Real-time streaming SLA (sub-second latency) | **on-demand** | A 2-minute reclaim mid-call is not OK |
| Demoing the engine for an afternoon | **spot** | Lowest cost; reclaim risk is fine |
| Building locally, no cloud | **`make dev`** | $0; no AWS at all |

The flag is the same on every command — `--spot` or `--on-demand`. Spot is the
default for `engine up`; on-demand is the default for the control plane in
`split` mode.

---

## What `--spot` actually does

When you launch with `--spot`, the script ([`infra/scripts/dalston-aws:1851`](../../infra/scripts/dalston-aws)):

1. Sorts the AZs in your region by **cheapest current spot price** and picks
   the cheapest one
2. Sets `InstanceMarketOptions.SpotOptions.InstanceInterruptionBehavior =
   "terminate"` (one-time spot, no hibernation)
3. Boots the instance with that market option

You'll see something like this in `dalston-aws status`:

```
GPU worker: dalston-gpu-faster-whisper
  instance: i-0abc... (g6.xlarge, spot=True, running)
  hostname: dalston-engine-faster-whisper
```

---

## What happens on a spot reclaim

AWS sends a 2-minute warning, then **terminates** the instance. As of today,
Dalston **does not** have automatic graceful drain or re-launch. Here's what
actually persists across the reclaim:

| Survives | Lost |
|---|---|
| Control-plane EBS `/data` in split mode (Postgres, Redis, API keys, audit log) | The instance itself |
| S3 artifacts (audio, transcripts, task outputs) | In-flight RAM state |
| Tailscale node identity (if it comes back) | Any job currently being processed by that engine |
| Job records in Postgres on the control plane | The GPU worker's local model cache and Redis registration |

To recover:

```bash
dalston-aws up                                      # control plane, if stopped
dalston-aws launch gpu --engines <preset> --spot    # split-mode GPU worker
# Single-engine mode uses: dalston-aws engine up <preset> --spot
```

The control plane stays durable; the replacement GPU worker is fresh. If models
are pre-staged in S3 it warms quickly, otherwise it falls back to HuggingFace.
See [13-spot-interruptions-recovery.md](13-spot-interruptions-recovery.md) for
the full recovery checklist.

---

## What `--on-demand` buys you

- **Stoppable.** `dalston-aws down` actually stops the instance — you only pay
  for EBS while it's stopped. Bring it back with `dalston-aws up`.
- **No reclaim risk.** AWS won't take it from you.
- **~3× the price.** Roughly. Use the [cost estimator](51-aws-cost-estimator.md)
  to compute per-preset rates.

If you're running a customer-facing API, this is what you want for the
control plane. The GPU workers can still be spot — the orchestrator will
re-route work to whichever engines are alive.

---

## Two ways to run engines

There are two operational shapes. Both use the same image and engine code; the
difference is whether you also have a control plane.

### A. Single engine, Tailscale-only — `dalston-aws engine up`

One EC2 box, one engine container, reachable over Tailscale at
`http://dalston-engine-<preset>:9100`. No gateway, no orchestrator. You talk
to it directly from your laptop SDK or curl.

```bash
dalston-aws setup -t gpu       # one-time: keypair, SG, IAM
dalston-aws engine up onnx --spot
# → Engine URL: http://dalston-engine-onnx:9100
dalston-aws engine status
dalston-aws engine down
```

This is the **lowest-cost** way to use a GPU. Nothing else is running.

Walkthrough: [11-single-engine-tailscale-mode.md](11-single-engine-tailscale-mode.md).

### B. Whole stack — `dalston-aws launch`

A control plane box (Gateway + Orchestrator + Redis + Postgres + CPU engines,
on-demand) plus one or more GPU workers (on spot). The control plane gives you
job DAGs, ElevenLabs/OpenAI API compatibility, web console, webhooks,
multi-tenant API keys.

```bash
dalston-aws setup -t split
dalston-aws launch              # both pieces from the template
```

Walkthrough: [21-control-plane-aws-deploy.md](21-control-plane-aws-deploy.md).

---

## Cost intuition (not a quote)

Verified rough numbers from [`docs/guides/aws-deploy.md`](aws-deploy.md) and
the AWS pricing pages:

| Setup | On-demand | Spot |
|---|---|---|
| Control plane only (t3.large) | ~$60/mo | ~$20/mo |
| Single GPU box (g4dn.xlarge) | ~$380/mo | ~$130/mo |
| Single GPU box (g6.xlarge) | ~$760/mo | ~$250/mo |
| **Split: t3.large on-demand + g6.xlarge spot** | — | **~$87/mo** ⭐ |
| Single g5.xlarge on-demand 24/7 | ~$725/mo | — |

The big unlock: split mode pins the small CPU box on-demand (so the API stays
up) and runs the expensive GPU on spot. Same throughput per dollar as a single
on-demand g5, at roughly **1/8th the cost**.

Full breakdown: [51-aws-cost-estimator.md](51-aws-cost-estimator.md).

---

## See also

- [11-single-engine-tailscale-mode.md](11-single-engine-tailscale-mode.md) — `engine up` walkthrough
- [12-engine-presets-catalog.md](12-engine-presets-catalog.md) — what each preset is and what it costs
- [13-spot-interruptions-recovery.md](13-spot-interruptions-recovery.md) — full reclaim recovery
- [50-performance-and-rtf.md](50-performance-and-rtf.md) — sizing math
