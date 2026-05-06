# AWS cost estimator

> Dalston is designed to make GPU compute disposable. Spin a GPU up for an
> afternoon, transcribe a podcast back catalog, spin it down. The cheapest
> 24/7 ElevenLabs/OpenAI-compatible API you can self-host costs about
> **$87/month** all-in.

This page collects the verified cost numbers from the codebase
([`docs/guides/aws-deploy.md`](aws-deploy.md), the `infra/templates/*.yaml`
files, and current AWS pricing pages) into one practical reference. Real
prices vary by region and time — these are eu-west-2 (London) on-demand
ranges as of late 2025.

---

## What you actually pay for

| Resource | What | Why it costs |
|---|---|---|
| **EC2 instance hours** | The compute | Largest line item by far |
| **EBS volume** | `/data` (Postgres, Redis, models cache) | ~$4/mo for 50 GB gp3 |
| **S3 storage** | Audio + transcripts + task artifacts | A few cents/mo per podcast hour |
| **S3 requests** | PUT/GET on artifacts | Negligible at typical volumes |
| **Egress** | Bytes leaving AWS | Negligible for STT (audio in is the heavy direction) |
| **NAT / VPC** | Not used | Dalston uses public subnets + Tailscale |

For most users, **EC2 dominates**. Optimizing it = optimizing total cost.

---

## Instance hourly rates (eu-west-2)

| Instance | vCPU | RAM | GPU | On-demand $/hr | Spot $/hr (typical) |
|---|---|---|---|---|---|
| t3.large | 2 | 8 GB | — | $0.0832 | ~$0.025 |
| t3.xlarge | 4 | 16 GB | — | $0.1664 | ~$0.05 |
| g4dn.xlarge | 4 | 16 GB | T4 (16 GB VRAM) | $0.526 | ~$0.16–0.21 |
| g5.xlarge | 4 | 16 GB | A10G (24 GB) | $1.006 | ~$0.34 |
| g6.xlarge | 4 | 16 GB | L4 (24 GB) | $1.05 | ~$0.34 |
| p3.2xlarge | 8 | 61 GB | V100 (16 GB) | $3.06 | varies |

> **Spot ≈ 65–70% off on-demand** is the rule of thumb — applies on every
> instance class. It's not stable: the AZ price algorithm in `dalston-aws`
> picks the cheapest AZ at launch and locks that price for the life of the
> instance.

Confirm current prices with `aws ec2 describe-spot-price-history` or the
console.

---

## Common deployments — full monthly cost

These are **730 hours/month** (24/7) all-in numbers including EBS and S3 at
typical usage. Verified against [`docs/guides/aws-deploy.md`](aws-deploy.md).

| Deployment | Instances | Pricing | ~$/mo |
|---|---|---|---|
| `make dev` | none (your laptop) | — | **$0** |
| Single CPU box | t3.xlarge | on-demand | ~$120 |
| Single CPU box | t3.xlarge | spot | ~$40 |
| Single GPU | g4dn.xlarge | on-demand | ~$385 |
| Single GPU | g5.xlarge | on-demand | ~$725 |
| Single GPU | g6.xlarge | spot | ~$255 |
| **Split: t3.large + g6.xlarge** | both | on-demand + spot | **~$87** ⭐ |
| Split: t3.large + g5.xlarge | both | on-demand + spot | ~$310 |

**Split mode is the headline.** A small CPU box runs the gateway, orchestrator,
Postgres, Redis, and CPU engines — always-on so the API stays reachable. The
GPU worker runs on spot, with a model cache on `/data` so reclaim recovery is
fast. Total: roughly 1/8th the cost of a single g5.xlarge on-demand for the
same throughput class.

---

## "On-the-go" usage — pay-per-use

If you only need transcription occasionally, billing is per-second. Spin the
GPU up, run the job, spin it down. Practical examples:

| Workload | Setup | Time on the clock | Cost |
|---|---|---|---|
| One 1-hour podcast, faster-whisper | `engine up faster-whisper --spot` (g4dn.xlarge) | 30 min wall (5-min boot + 25-min RTF 0.4) | ~$0.10 |
| Same, on NeMo (English only) | `engine up nemo --spot` | 6 min wall (5-min boot + 1-min RTF 0.0006 + safety) | ~$0.02 |
| Voxtral Q&A on a 30-min recording | `engine up vllm-asr --spot` (g6.xlarge) | 15 min wall | ~$0.10 |
| Diarize 100 hours of archive overnight | `engine up pyannote --spot` | ~15 hours of clock time at RTF 0.15 | ~$3 |
| Batch transcribe 1,000 hours of catalog | `engine up nemo --spot` | ~1 hour wall on a g4dn.xlarge spot | ~$0.20 |

> **The killer feature:** these costs assume you actually `engine down` when
> done. Set a calendar reminder. AWS happily bills your spot instance forever.

---

## Storage & artifacts

- **EBS** (`/data` on the control plane): 50 GB gp3 ≈ $4/mo. Holds Postgres,
  Redis dumps, model cache. Do **not** delete this — it's where the cold-start
  goes after the first model download.
- **S3** (audio + transcripts): ~$0.023/GB/mo + GET/PUT charges. A typical
  podcast (1-hour 192 kbps mp3) is ~85 MB; transcript JSON is ~50 KB.
  100 podcasts ≈ 8.5 GB ≈ $0.20/mo.
- **Set retention** to control storage: the API and SDK take a `retention=`
  parameter (0 = transient, -1 = permanent, N = days). Default is 30 days.

---

## Cost levers, ranked by impact

1. **`dalston-aws down` overnight.** Cuts a 24/7 GPU bill by 2/3 if you only
   work daytime hours. The `down` command stops on-demand instances; spot
   instances get terminated and you re-launch with `up`.
2. **Use spot for GPU workers.** ~65% off. The control plane stays on-demand
   so the API is up.
3. **Pick the right preset.** A NeMo job on g4dn.xlarge spot finishes in
   1/50th the wall-clock time of a Whisper job on the same box, for 1/50th the
   cost. Match preset to language and accuracy needs — see
   [12-engine-presets-catalog.md](12-engine-presets-catalog.md).
4. **Right-size the GPU.** g4dn.xlarge (T4) is fine for ONNX, faster-whisper,
   pyannote, and small NeMo. Step up to g6.xlarge (L4) only when you need
   compute ≥ 8.0 (vllm-asr) or co-location.
5. **Clean up S3.** Set a 30-day retention default, lifecycle-archive older
   audio.

---

## What it does **not** cost

- **Per-minute transcription fees.** You own the model, you own the box. The
  $87/mo split-mode setup gives you unlimited transcription up to whatever
  the box can sustain.
- **Per-API-call fees.** No throttling on tokens or characters.
- **Stripe/billing/auth/SaaS markup.** This is your AWS bill, nothing else.

For comparison, ElevenLabs Scribe is ~$0.40/hr of audio, OpenAI Whisper API
is $0.006/min ≈ $0.36/hr. **Dalston breaks even at ~30 audio-hours/month**
versus those APIs, and gets cheaper from there.

---

## See also

- [10-engines-spot-and-on-demand.md](10-engines-spot-and-on-demand.md) — the spot vs on-demand mental model
- [12-engine-presets-catalog.md](12-engine-presets-catalog.md) — preset-by-preset price/perf
- [50-performance-and-rtf.md](50-performance-and-rtf.md) — RTF math
- [52-cost-correlate-tool.md](52-cost-correlate-tool.md) — daily cost-per-episode reports via `dalston-cost-correlate`
- [aws-cost-correlation.md](aws-cost-correlation.md) — engineering reference for the cost correlation tool
