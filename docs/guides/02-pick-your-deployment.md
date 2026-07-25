# Pick your deployment

Five paths run from local evaluation to a multi-worker self-hosted
ElevenLabs/OpenAI alternative. Pick the smallest one that fits.

If you've already read [01-quickstart.md](01-quickstart.md), you've used path

1. This page lays out the rest, with the cost and ergonomics tradeoffs.

---

## Decision tree

```
Do you need a GPU?
├── No (CPU is fine)
│   ├── Just want to try it? ────────────► 1. make dev
│   └── Need a small hosted API? ────────► 2. CPU-only AWS box
│
└── Yes
    ├── Just for this session? ──────────► 3. Single-engine Tailscale mode
    ├── Need a durable API + GPU? ───────► 4. Split mode
    └── Need many GPUs / multi-engine? ──► 5. Split + multiple engine workers
```

---

## 1. Local dev — `make dev`

**You get:** the full stack on your laptop, CPU-only engines.
**You don't get:** GPU-class engines (NeMo, vllm-asr) which require NVIDIA.

```bash
make dev
```

Runs Postgres, Redis, MinIO, gateway (8000), orchestrator, and CPU engines via
Docker Compose. Use this for development, integration tests, and demos.

---

## 2. CPU-only AWS — `setup -t cpu`

**You get:** always-on REST + WebSocket API, accessible over Tailscale.
**You don't get:** GPU performance.

```bash
dalston-aws setup -t cpu
dalston-aws launch
```

When this is right: the selected CPU engines meet your measured latency and
throughput requirements.

---

## 3. Single-engine, Tailscale-only — `engine up`

**You get:** one engine container on one EC2 GPU box, reachable from your laptop.
**You get:** native synchronous HTTP plus minimal OpenAI/ElevenLabs-compatible
routes on transcription engines.
**You don't get:** the gateway, web console, durable jobs, multi-tenant auth,
webhooks, exports, or multi-stage DAG assembly.

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

**You get:** the full system. Always-on REST API, ElevenLabs and OpenAI
compatibility layers, real-time WebSocket streaming, job DAG, web console,
webhooks, multi-tenant API keys.
**You give up:** spot reclaim risk on the GPU worker (mitigated by EBS
persistence + control plane resilience).

```bash
dalston-aws setup -t split
dalston-aws launch control-plane
dalston-aws launch gpu --engines nemo,pyannote --spot
```

When this is right: you want a hosted-grade STT API for production traffic.
This is the default full-stack recommendation.

Walkthrough: [21-control-plane-aws-deploy.md](21-control-plane-aws-deploy.md).

---

## 5. Split + multiple engine workers

**You get:** parallel processing — multiple engines on multiple GPUs,
horizontally scaled.

```bash
dalston-aws setup -t split
dalston-aws launch control-plane
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
| align, prepare, redact | yes | — |

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

Dalston is a good fit when at least one of these is true:

- You have data residency, privacy, or compliance reasons to keep audio on
  your own infrastructure.
- You want bursty GPU compute (spin up, transcribe a big batch, spin down).
- You want to mix engines — Whisper for some languages, NeMo for others, Voxtral for an audio LLM use-case — without juggling different vendors.
- You want offline / air-gapped transcription (CPU stack works fully offline once models are cached).

Compare current infrastructure and hosted-API pricing using your measured
volume; there is no universal break-even point.

---

## See also

- [01-quickstart.md](01-quickstart.md) — get a transcript out in 5 minutes
- [51-aws-cost-estimator.md](51-aws-cost-estimator.md) — deeper pricing
- [10-engines-spot-and-on-demand.md](10-engines-spot-and-on-demand.md) — spot/on-demand mental model
- [aws-deployment-scenarios.md](aws-deployment-scenarios.md) — original engineering reference, more scenarios
