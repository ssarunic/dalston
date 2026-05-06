# `dalston-cost-correlate` — daily cost-per-episode reports

> Did yesterday's $40 GPU spike correspond to extra episodes, or to idle
> instances? `dalston-cost-correlate` joins AWS Cost Explorer, CloudTrail,
> and your Postgres jobs table to give you a per-day breakdown.

This is the tool to use when you want to **prove** the economics of your
self-hosted setup, decide whether to switch presets, or hunt down idle
spend.

> **Engineering reference:** [aws-cost-correlation.md](aws-cost-correlation.md).
> This page is the salesy walkthrough.

---

## What it tells you

For every day in the window, the script computes:

| Metric | Meaning |
|---|---|
| `jobs_completed` | Episodes finished that day |
| `audio_hours` | Audio duration transcribed |
| `useful_work_s` | Sum of GPU-stage task wall times (the work you wanted to pay for) |
| `billed_instance_s` | Sum of EC2 instance lifetimes (what AWS actually charged you for) |
| `warmup_overhead_s` | `billed - useful` — idle, boot, model-load time |
| `warmup_ratio` | `warmup / billed` — fraction of paid time that wasn't useful |
| `cost_per_episode` | Total daily cost / jobs_completed |
| `cost_per_audio_hour` | Total daily cost / audio_hours |

---

## Why you care

Three concrete examples:

1. **"Is split mode actually $87/mo?"**
   Run the tool over a month. Sum the `cost` column. If it's notably higher,
   investigate `warmup_ratio` and `billed_instance_s` — you might be paying
   for instances that aren't doing work.

2. **"Should I switch from faster-whisper to NeMo?"**
   Compare `cost_per_audio_hour` before and after. NeMo's RTF 0.0006 means
   the GPU is busy a lot less of the time — same instance hours, far more
   audio per hour. The line item moves from $0.30/hr to $0.05/hr.

3. **"How much money am I leaking on idle GPU?"**
   Look at `warmup_ratio`. >50% means most of your spend is *not* useful
   work. Either set `dalston-aws down` overnight, or switch the GPU worker
   to bigger batch jobs that fill the day.

---

## Prerequisites

1. **AWS credentials** with read access to `ce:GetCostAndUsage`,
   `cloudtrail:LookupEvents`, `ec2:DescribeInstances`.

2. **`Project` cost-allocation tag activated** in your AWS billing console:
   <https://console.aws.amazon.com/billing/home#/tags> → enable `Project`
   under "User-defined cost allocation tags." This is a one-time setup,
   takes ~24 hours to start populating.

3. **Postgres access.** The tool reads `DATABASE_URL` from your env. If
   running from your laptop, SSH-tunnel:

   ```bash
   ssh -L 5432:localhost:5432 ubuntu@<dalston-host> -N &
   export DATABASE_URL=postgresql://dalston:password@localhost:5432/dalston
   ```

4. **Python dependencies.** The repo venv has `boto3` and `asyncpg`:

   ```bash
   .venv/bin/python ./infra/scripts/dalston-cost-correlate --help
   ```

---

## Usage

### Last 14 days, summary to terminal

```bash
.venv/bin/python ./infra/scripts/dalston-cost-correlate --days 14
```

CSV to stdout, human summary to stderr:

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

### Different region

```bash
.venv/bin/python ./infra/scripts/dalston-cost-correlate --region us-east-1 --days 7
```

### No `Project` tag set up yet

Whole-account spend instead of just Dalston:

```bash
.venv/bin/python ./infra/scripts/dalston-cost-correlate --days 30 --no-tag-filter
```

---

## Reading the output

A typical row:

```csv
day,jobs_completed,audio_hours,useful_work_s,billed_instance_s,warmup_overhead_s,warmup_ratio,total_cost_usd,cost_per_episode,cost_per_audio_hour
2026-04-15,28,12.4,1280,2160,880,0.41,3.71,0.132,0.299
```

What this says:

- 28 episodes done, 12.4 audio hours
- 21 minutes of useful GPU work, 36 minutes of billed instance time
- 41% of that paid time was warmup / idle
- $3.71 spent ⇒ $0.13/episode, $0.30/audio-hour

If `warmup_ratio` were under 20%, you're efficient. Over 50% means you're
booting instances for short bursts and paying for the warm-up tax — batch
work tighter together.

---

## Cron it as a daily report

```bash
# Daily 6am report — append to a CSV
0 6 * * * cd /home/dalston && .venv/bin/python infra/scripts/dalston-cost-correlate \
    --days 1 --output - >> /var/log/dalston-cost.csv 2>/dev/null
```

Or pipe into a Slack webhook for a daily standup-friendly dashboard.

---

## Caveats

- **Cost Explorer has a ~24-hour delay** — yesterday's costs aren't in the
  data yet at 9am today. Run on T-2 or later for stable numbers.
- **Tag activation is not retroactive.** If you only enabled the `Project`
  tag last week, anything before then needs `--no-tag-filter` to count.
- **CloudTrail events expire after 90 days** in the default trail. For
  longer windows, you need a long-retention trail or an Athena setup.
- **The script uses `unblended` cost.** Reserved instance amortization isn't
  reflected — appropriate for spot-heavy deployments where there's nothing
  reserved.

---

## See also

- [aws-cost-correlation.md](aws-cost-correlation.md) — engineering reference
- [51-aws-cost-estimator.md](51-aws-cost-estimator.md) — the static estimator
- [50-performance-and-rtf.md](50-performance-and-rtf.md) — the RTF math the tool quantifies in dollars
- [10-engines-spot-and-on-demand.md](10-engines-spot-and-on-demand.md) — strategies to reduce warmup ratio
