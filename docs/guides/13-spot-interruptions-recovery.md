# Spot interruptions and recovery

> Spot is wonderful until AWS takes your instance back. This page is the
> honest answer about what happens when that occurs in Dalston, what
> persists, and what you do about it.

---

## What "reclaim" actually means here

When AWS needs spot capacity back, it sends a **2-minute warning** and then
takes the instance. In Dalston's setup, that means **terminate, not stop**.
The script forces this on purpose:

> [`infra/scripts/dalston-aws:1896`](../../infra/scripts/dalston-aws):
> `InstanceMarketOptions.SpotOptions.InstanceInterruptionBehavior: "terminate"`

And in the stop path ([`infra/scripts/dalston-aws:2934`](../../infra/scripts/dalston-aws)):

```python
if "UnsupportedOperation" in err_str and "Spot" in err_str:
    warn(f"{instance_id}: one-time spot instance — terminating instead")
    ec2.terminate_instances(InstanceIds=[instance_id])
```

So `dalston-aws down` on a spot instance terminates it. AWS reclaim does the
same. Either way, the instance is gone.

---

## What persists vs what's lost

| ✅ Survives reclaim | ❌ Lost on reclaim |
|---|---|
| EBS `/data` volume on the **control plane** (in split mode) | The terminated instance itself |
| S3 audio + transcripts + task artifacts | RAM state of the GPU worker |
| Postgres job records on the control plane | Any job currently being processed |
| API keys, webhooks, audit log | Worker registration in Redis (TTLs out automatically) |
| Tailscale node identity (if you reuse the hostname) | Per-engine-worker EBS in single-engine mode |

> **Important cache caveat:** GPU worker model caches are local to the worker
> instance. In single-engine mode and split-mode GPU workers, a reclaim means
> the local cache is gone. Pre-stage models in S3 if you want replacements to
> warm quickly without hitting HuggingFace.

---

## Recovery paths

### A. Split mode — control plane is up, GPU worker died

This is the easy case.

```bash
dalston-aws status
# Will show: GPU worker: dalston-gpu-faster-whisper (instance: i-... terminated)

dalston-aws launch gpu --engines faster-whisper --spot
# Or, if the worker was launched via 'engine up':
dalston-aws engine up faster-whisper --spot
```

What happens behind the scenes:

1. New EC2 boots, joins Tailscale with the same hostname
2. `/data` is **not** the same EBS — it's a fresh attached volume per launch
   for GPU workers. The control plane's `/data` is what persists.
3. Engine container starts. First-run model download proceeds normally
   (S3-first, HF fallback).
4. Engine registers with the gateway's Redis registry. Any pending jobs in
   the orchestrator's task queue start flowing again.
5. Jobs that were *in-flight* on the dead worker are detected as stale by
   the orchestrator's `StaleTaskScanner` and re-queued.

### B. Split mode — control plane itself died (unlikely)

The control plane runs on **on-demand** in `split.yaml` (`spot: false`), so
this only happens if AWS does maintenance, you stop it explicitly, or
something catastrophic. The state on `/data` (Postgres + Redis dumps + model
cache) is on a separate EBS volume and survives a stop.

```bash
dalston-aws up      # restarts the control plane and GPU worker(s)
```

### C. Single-engine mode — GPU box died

```bash
dalston-aws engine status
# preset:        faster-whisper
# instance:      i-0abc... (terminated)

dalston-aws engine down       # cleans up local state
dalston-aws engine up faster-whisper --spot     # fresh launch
```

Models will re-download on first request (no persistent EBS in this mode).

---

## What is **not** automatic (yet)

Be honest about the limits:

- **No graceful drain on the 2-minute warning.** Dalston does not currently
  watch for `instance-action: terminate` IMDSv2 events and gracefully bail
  out of in-flight jobs. A reclaim will kill whatever was processing.
- **No auto re-launch.** AWS won't replace the spot instance for you. You
  re-run `dalston-aws engine up` or `dalston-aws launch gpu`.
- **No spot fleet / capacity rebalancing.** One instance, one launch.

These are roadmap items. If you need real-time SLA guarantees that survive a
reclaim, run the GPU worker on `--on-demand`.

---

## Minimizing reclaim pain

1. **Pre-stage models in S3.** Replacement GPU workers are fresh, so S3 is the
   durable model cache across reclaims. It avoids slow HuggingFace downloads
   and keeps launches predictable.
2. **Run multiple GPU workers across AZs.** AWS reclaims are usually
   per-AZ. Two workers in two AZs means at most one dies at a time.

   ```bash
   dalston-aws launch gpu --engines nemo --spot --region eu-west-2
   dalston-aws launch gpu --engines nemo --spot --region eu-west-1
   ```

3. **Keep batch jobs idempotent.** The orchestrator already retries failed
   tasks on a different engine when capability matches. Submit batches with
   reasonable retry budgets.
4. **For real-time, prefer on-demand.** A streaming session is not friendly
   to a 2-minute warning. The cost of `--on-demand` for a streaming-only
   GPU worker is usually worth it.

---

## Detecting stale workers

The Redis worker registry (`dalston:engine:instance:{id}`) has a 60-second
TTL. A dead worker silently disappears within a minute. The orchestrator's
`StaleTaskScanner` (in [`dalston/orchestrator/`](../../dalston/orchestrator/))
re-queues tasks that were assigned to the missing worker.

Symptoms of a stuck job after a reclaim that didn't recover cleanly:

- Job stays in `running` for > 5 minutes
- `dalston-aws status` shows the GPU worker as terminated but `/v1/engines`
  still lists it (TTL hasn't expired yet — wait 60s)
- Orchestrator logs show repeated `task.wait_timeout` for the same task

Manual recovery:

```bash
dalston jobs cancel <job_id>
dalston jobs get <job_id>      # confirm cancelled
# ...then resubmit
```

---

## See also

- [10-engines-spot-and-on-demand.md](10-engines-spot-and-on-demand.md) — pricing model
- [11-single-engine-tailscale-mode.md](11-single-engine-tailscale-mode.md) — single-engine mode caveats
- [21-control-plane-aws-deploy.md](21-control-plane-aws-deploy.md) — split-mode deploy
- [aws-deploy.md](aws-deploy.md) — engineering reference
