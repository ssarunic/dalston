# Pick your deployment

> Five paths from "playing on my laptop" to "production-grade self-hosted
> ElevenLabs/OpenAI alternative." Pick the smallest one that fits.

If you've already read [01-quickstart.md](01-quickstart.md), you've used path

1. This page lays out the rest, with the cost and ergonomics tradeoffs.

---

## Decision tree

```
Do you need a GPU?
├── No (CPU is fine)
│   ├── Just want to try it? ────────────► 1. make dev (laptop, $0)
│   └── Need 24/7 API for a small team? ─► 2. CPU-only AWS box (~$120/mo)
│
└── Yes
    ├── Just for this afternoon? ────────► 3. Single-engine Tailscale mode (~$0.20/hr spot)
    ├── Need a 24/7 API + GPU? ──────────► 4. Split mode (CPU + GPU spot, ~$87/mo)
    └── Need many GPUs / multi-engine? ──► 5. Split + multiple engine workers
```

---

## 1. Local dev — `make dev`

**Cost:** $0
**You get:** the full stack on your laptop, CPU-only engines.
**You don't get:** GPU-class engines (NeMo, vllm-asr) which require NVIDIA.

```bash
make dev
```

Runs Postgres, Redis, MinIO, gateway (8000), orchestrator, and CPU engines via
Docker Compose. `dalston transcribe meeting.mp3` works immediately.

Use this for: development, integration tests, demos. The CPU engine path uses
faster-whisper on CPU at RTF 0.4 — a 1-hour file in roughly 2.5 hours wall.
Fine for testing; not fine for production throughput.

---

## 2. CPU-only AWS — `setup -t cpu`

**Cost:** ~$120/mo on-demand, ~$40/mo spot ([t3.xlarge](../../infra/templates/cpu.yaml))
**You get:** always-on REST + WebSocket API, accessible over Tailscale.
**You don't get:** GPU performance.

```bash
dalston-aws setup -t cpu
dalston-aws launch
```

When this is right: you're transcribing low-volume traffic (<10 hours/day),
your audio is short, and you don't care if a 1-hour file takes 25 minutes to
finish. Or you're using **`onnx`** which CPU-runs at RTF 0.12 — actually
viable for a lot of workloads.

---

## 3. Single-engine, Tailscale-only — `engine up`

**Cost:** spot rate × hours actually used. ~$0.20/hr for g4dn.xlarge spot.
**You get:** one engine container on one EC2 GPU box, reachable from your laptop.
**You don't get:** REST API, ElevenLabs/OpenAI compat, web console, multi-tenant auth, job DAG (no align/diarize/merge stitching).

```bash
dalston-aws setup -t gpu        # one-time
dalston-aws engine up faster-whisper --spot
# → http://dalston-engine-faster-whisper:9100
dalston-aws engine down         # when done
```

When this is right: you transcribe in batches, irregularly, and want to pay
only for compute you used. Best $/hour rate of any GPU mode.
Walkthrough: [11-single-engine-tailscale-mode.md](11-single-engine-tailscale-mode.md).

---

## 4. Split mode — `setup -t split`

**Cost:** ~$87/mo (t3.large on-demand + g6.xlarge spot — see
[`infra/templates/split.yaml`](../../infra/templates/split.yaml)).
**You get:** the full system. Always-on REST API, ElevenLabs and OpenAI
compatibility layers, real-time WebSocket streaming, job DAG, web console,
webhooks, multi-tenant API keys.
**You give up:** spot reclaim risk on the GPU worker (mitigated by EBS
persistence + control plane resilience).

```bash
dalston-aws setup -t split
dalston-aws launch
```

When this is right: you want a hosted-grade STT API for production traffic.
This is the **default recommendation**. It's the cheapest 24/7 GPU-backed
self-hosted ElevenLabs alternative we know how to build.

Walkthrough: [21-control-plane-aws-deploy.md](21-control-plane-aws-deploy.md).

---

## 5. Split + multiple engine workers

**Cost:** $87/mo + $250/mo per additional GPU worker (typical).
**You get:** parallel processing — multiple engines on multiple GPUs,
horizontally scaled.

```bash
dalston-aws setup -t split
dalston-aws launch                    # control plane + 1 GPU worker
dalston-aws launch gpu --engines nemo,pyannote          # add a co-located worker
dalston-aws launch gpu --engines vllm-asr --gpu-type g6.xlarge --on-demand   # add a stable Voxtral box
```

When this is right: you're running real-time streams during business hours
(steady GPU load) plus batch nightly catalog jobs (burst load), or you want
distinct GPU types for distinct preset families. The orchestrator routes work
to engines based on capability, model, and current load — see
[20-control-plane-tour.md](20-control-plane-tour.md).

---

## Cross-cutting decisions

These apply to every path:

### CPU vs GPU model choice

| | CPU OK | GPU only |
|---|---|---|
| transcribe | onnx, faster-whisper, hf-asr, hf-asr-align-pyannote | nemo, vllm-asr |
| diarize | pyannote (slow), hf-asr-align-pyannote | — |
| align, prepare, merge, redact | yes | — |

### Spot vs on-demand

- **Always spot for one-shot batch.**
- **On-demand for the control plane in split mode.**
- **Spot for GPU workers** unless you have a real-time SLA that doesn't
  tolerate a 2-minute reclaim.

### Region

The default is `eu-west-2` (London). Change in
[`infra/templates/*.yaml`](../../infra/templates/) `region:` field, or use
`--region` on `setup`. Pick one with the GPU instance type you want — not all
regions have g6 / L4 yet.

---

## What makes Dalston a good fit

Dalston wins when **at least one** of these is true:

- You transcribe more than ~30 hours/month (cheaper than ElevenLabs/OpenAI APIs).
- You have data residency, privacy, or compliance reasons to keep audio on
  your own infrastructure.
- You want bursty GPU compute (spin up, transcribe a big batch, spin down).
- You want to mix engines — Whisper for some languages, NeMo for others, Voxtral for an audio LLM use-case — without juggling different vendors.
- You want offline / air-gapped transcription (CPU stack works fully offline once models are cached).

Where you might pick something else:

- You transcribe 5 minutes of audio a month — just use the ElevenLabs or
  OpenAI API, the per-minute price wins.
- You don't run any AWS infrastructure today — the on-ramp is steeper than a
  hosted SaaS.

---

## See also

- [01-quickstart.md](01-quickstart.md) — get a transcript out in 5 minutes
- [51-aws-cost-estimator.md](51-aws-cost-estimator.md) — deeper pricing
- [10-engines-spot-and-on-demand.md](10-engines-spot-and-on-demand.md) — spot/on-demand mental model
- [aws-deployment-scenarios.md](aws-deployment-scenarios.md) — original engineering reference, more scenarios
