# Correlating AWS Cost with Transcription Activity

A guide to using `dalston-cost-correlate` to answer questions like:

- How much does it cost me to transcribe one episode?
- How much of my GPU spend is wasted on instance warmup vs. real work?
- Did yesterday's $40 spike correspond to extra episodes, or to idle instances?

The script joins three independent data sources and emits a daily CSV.

## What it does

| Source | What it provides |
|---|---|
| **AWS Cost Explorer** | Daily unblended cost in USD, filtered by the `Project=dalston` tag |
| **AWS CloudTrail** | `RunInstances` / `TerminateInstances` events → per-instance lifetimes |
| **Dalston Postgres** | Completed jobs (with `audio_duration`) and per-stage task durations |

For each day in the window it computes:

- `jobs_completed`, `audio_hours` — from the `jobs` table
- `transcribe_s`, `diarize_s`, `useful_work_s` — from the `tasks` table (sum of `completed_at - started_at` for GPU stages: `transcribe`, `align`, `diarize`, `pii_detect`, `audio_redact`)
- `billed_instance_s` — sum of EC2 lifetimes clipped to that calendar day
- `warmup_overhead_s` = `billed_instance_s - useful_work_s` (idle / boot / model-load time you paid for)
- `warmup_ratio`, `cost_per_episode`, `cost_per_audio_hour`

## Prerequisites

### 1. AWS credentials

The script uses `boto3`'s default credential chain. The IAM principal needs read access to:

- `ce:GetCostAndUsage`
- `cloudtrail:LookupEvents`
- `ec2:DescribeInstances`

```bash
aws sts get-caller-identity   # should print your account ID
```

### 2. Activate the `Project` cost allocation tag

Cost Explorer cannot filter by a tag until it has been **activated** as a Cost Allocation Tag. This is a one-time setup:

1. Open <https://console.aws.amazon.com/billing/home#/tags>
2. Find the `Project` tag in **User-defined cost allocation tags**
3. Click **Activate**

Activation is not retroactive — Cost Explorer starts attributing costs to the tag from the activation date forward, and there is roughly a 24-hour delay before data appears. Until then, run with `--no-tag-filter` to see whole-account totals.

### 3. Database access

The script reads the Dalston Postgres directly. It picks up `DATABASE_URL` from your environment (the same one in `.env`) and normalizes async URLs (`postgresql+asyncpg://…`) automatically.

If you're running it from a machine that can't reach the Postgres instance directly (e.g. the GPU box), open an SSH tunnel first:

```bash
ssh -L 5432:localhost:5432 ubuntu@<dalston-host> -N &
export DATABASE_URL=postgresql://dalston:password@localhost:5432/dalston
```

### 4. Python dependencies

`boto3` plus one Postgres driver. The project venv already has `asyncpg` installed, so this works out of the box:

```bash
.venv/bin/python ./infra/scripts/dalston-cost-correlate --help
```

If you prefer a system-wide install: `pip install boto3 'psycopg[binary]'`.

## Usage

### Last 14 days, summary to terminal

```bash
.venv/bin/python ./infra/scripts/dalston-cost-correlate --days 14
```

The CSV goes to stdout; a human-readable summary goes to stderr at the end:

```
--- summary ---
  days:                 14
  jobs completed:       312
  audio transcribed:    142.4 h
  useful GPU work:      18.3 h
  billed instance time: 27.6 h
  warmup overhead:      9.3 h (33.7%)
  total cost:           $52.18
  $/episode (avg):      $0.167
  $/audio-hour (avg):   $0.366
```

### Save a CSV for spreadsheet analysis

```bash
.venv/bin/python ./infra/scripts/dalston-cost-correlate \
    --start 2026-04-01 --end 2026-04-28 \
    --output /tmp/dalston-cost.csv
```

### Specify a different region

```bash
.venv/bin/python ./infra/scripts/dalston-cost-correlate --region us-east-1 --days 7
```

Cost Explorer itself is global (the script always calls it via `us-east-1`), but EC2 and CloudTrail are regional. Use whichever region you launched Dalston in.

### Whole-account costs (tag not activated yet)

```bash
.venv/bin/python ./infra/scripts/dalston-cost-correlate --days 30 --no-tag-filter
```

## Reading the output

| Column | Meaning |
|---|---|
| `date` | Calendar day (UTC) |
| `jobs_completed` | Jobs whose `completed_at` falls on this day |
| `audio_hours` | Sum of `audio_duration` for those jobs, in hours |
| `transcribe_s`, `diarize_s` | Sum of stage durations for tasks completed this day |
| `useful_work_s` | Sum of all GPU-stage task durations |
| `billed_instance_s` | Total EC2 instance-seconds you paid for this day |
| `warmup_overhead_s` | `billed_instance_s − useful_work_s` (boot, model load, idle) |
| `warmup_ratio` | `warmup_overhead_s / billed_instance_s` (0.0–1.0) |
| `cost_usd` | Daily unblended cost from Cost Explorer |
| `cost_per_episode` | `cost_usd / jobs_completed` |
| `cost_per_audio_hour` | `cost_usd / audio_hours` |

### What's a "good" warmup ratio?

It depends on your instance lifecycle pattern:

- **Always-on GPU instance, steady workload**: warmup is paid once and amortized → ratio approaches `0%` over time
- **Spot instance per batch, frequent interruptions**: every relaunch pays the full ~2-3 minute warmup (kernel boot + model download + model load to VRAM) → ratio can easily exceed `40%`
- **On-demand spin-up per job**: dominated by warmup → ratio often `>60%`

If your ratio is high and your job volume is low, keeping the instance running between jobs (or batching uploads) usually pays for itself.

## Caveats and limitations

1. **Daily granularity, not per-job**. The warmup figure is `total billed seconds − total useful work seconds` across all instances active that day. Good enough to answer "is warmup eating 30% or 5% of my spend"; not enough to attribute warmup to a specific episode. Per-instance attribution would require the orchestrator to record which host ran each task — it currently doesn't.

2. **Cost Explorer has ~24h delay**. Today's costs show up tomorrow.

3. **CloudTrail keeps 90 days free**. Older windows will be missing instance lifetimes (the script will still report costs but `billed_instance_s` will be understated).

4. **Tag activation is not retroactive**. Costs incurred before you activated the `Project` tag are not filtered — they'll appear under "untagged" in Cost Explorer. The script silently skips those days. Run with `--no-tag-filter` if you need historical totals.

5. **EBS, S3, data transfer**. The cost figure includes *all* costs tagged with `Project=dalston`, not just EC2. So `cost_per_episode` reflects your true marginal cost, but the warmup ratio is computed against EC2 instance-seconds only.

## Troubleshooting

**"DataUnavailableException" warning** — the `Project` tag isn't activated yet, or activation is <24h old. Wait, or use `--no-tag-filter`.

**`billed_instance_s` is 0 but cost is non-zero** — CloudTrail returned no `RunInstances` events in the window. Either the instances launched before the window started (extend `--start`), or the script lacks `cloudtrail:LookupEvents` permission.

**Postgres connection refused** — the script needs direct DB access. If you're running locally and Dalston runs on AWS, open an SSH tunnel (see prerequisites).

**Numbers seem high** — check whether you've left a spot instance running. `dalston-aws status` shows current instance state.

## See also

- [`infra/scripts/dalston-cost-correlate`](../../infra/scripts/dalston-cost-correlate) — the script itself
- [`docs/guides/aws-deploy.md`](aws-deploy.md) — provisioning AWS resources for Dalston
- [`docs/guides/aws-deployment-scenarios.md`](aws-deployment-scenarios.md) — instance type / spot tradeoffs
