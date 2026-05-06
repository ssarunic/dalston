# Single-engine, Tailscale-only mode — a GPU on the go

> Your laptop, a Tailscale tunnel, and a spot GPU you spin up for an
> afternoon. No control plane, no orchestrator, no Postgres. Just one engine
> container on one EC2 box, billed by the second.

This is the lowest-friction way to use Dalston with a GPU. It's the right
choice when you want raw transcription throughput and don't need the
multi-tenant control plane, web console, or job DAG. You talk to the engine's
HTTP endpoint directly.

---

## The shape

```
   Your laptop                          AWS (eu-west-2 or wherever)
   ┌─────────────┐                      ┌──────────────────────────┐
   │  dalston    │                      │   EC2 (g6.xlarge spot)   │
   │  CLI / SDK  │ ── Tailscale ──────► │   ┌────────────────────┐ │
   │  / curl     │  100.x.x.x mesh      │   │  one engine        │ │
   └─────────────┘                      │   │  e.g. nemo / vllm  │ │
                                        │   │  http://...:9100   │ │
                                        │   └────────────────────┘ │
                                        └──────────────────────────┘
```

No public IPs are exposed. The instance only joins your Tailscale tailnet, and
its hostname is `dalston-engine-<preset>` (e.g. `dalston-engine-vllm-asr`).

---

## Prerequisites

1. **AWS CLI** with credentials. `aws sts get-caller-identity` should work.
2. **Tailscale** running on your laptop, joined to your tailnet.
3. The `dalston-aws` script on your `PATH`:

   ```bash
   ln -s "$(pwd)/infra/scripts/dalston-aws" /usr/local/bin/dalston-aws
   ```

4. **HuggingFace token** (`HF_TOKEN`) if you'll use the `pyannote` preset for
   diarization — the model is gated. See [30-how-models-are-fetched.md](30-how-models-are-fetched.md).

---

## One-time setup

The `engine up` command reuses the keypair, GPU security group, and IAM role
created by `setup -t gpu`. Run that first:

```bash
dalston-aws setup -t gpu
```

This creates an S3 bucket (unused in single-engine mode but harmless), an IAM
instance profile, an SSH keypair, and a security group locked to Tailscale CIDR
ranges. It does **not** boot any instance yet.

---

## Launching an engine

Pick a preset (full catalog: [12-engine-presets-catalog.md](12-engine-presets-catalog.md)):

| Preset | What it does | GPU floor | Default model |
|---|---|---|---|
| `onnx` | Parakeet ONNX, lightweight, also runs OK on CPU | 2 GB VRAM | parakeet-tdt-0.6b ONNX |
| `faster-whisper` | Whisper large-v3-turbo via CTranslate2, 99 langs | 4 GB VRAM | `large-v3-turbo` |
| `nemo` | Parakeet TDT 0.6B-v3 — fastest English, native streaming | 4 GB VRAM | `nvidia/parakeet-tdt-0.6b-v3` |
| `pyannote` | Speaker diarization (pyannote 4.0, Community-1) | 2 GB VRAM | needs `HF_TOKEN` |
| `vllm-asr` | Voxtral Mini 3B — multilingual audio LLM | 8 GB VRAM, compute ≥ 8.0 | `mistralai/Voxtral-Mini-3B-2507` |
| `hf-asr` | Any HuggingFace ASR model with `pipeline_tag=automatic-speech-recognition` | 4 GB VRAM | `openai/whisper-large-v3` |

Launch:

```bash
dalston-aws engine up faster-whisper --spot
# [dalston-aws] Launching faster-whisper on g4dn.xlarge (spot) in eu-west-2...
# [dalston-aws] Engine 'faster-whisper' launched: i-0abc1234567890def
# [dalston-aws] Tailscale hostname: dalston-engine-faster-whisper
# [dalston-aws] Engine URL: http://dalston-engine-faster-whisper:9100
# [dalston-aws] Bootstrap takes ~3-5 minutes.
```

`--spot` is the default; `--on-demand` is the alternative. Pick the GPU
instance type explicitly with `--gpu-type g6.xlarge` if you want something
other than the template default.

> **vllm-asr won't fit on T4.** The `vllm-asr` preset declares
> `min_gpu_compute: 8.0` because Flash Attention 2 needs Ampere or newer.
> `dalston-aws` rejects T4 (compute 7.5) for this preset. Use `--gpu-type
> g6.xlarge` (L4, compute 8.9) or `--gpu-type g5.xlarge` (A10G, compute 8.6).

---

## Watching it come up

```bash
dalston-aws engine status
# preset:        faster-whisper
# instance:      i-0abc1234... (running)
# instance type: g4dn.xlarge
# pricing:       spot
# hostname:      dalston-engine-faster-whisper
# health:        starting (model downloading)

# After 3-5 min:
# health:        ok
```

What's actually happening during those 3–5 minutes:

1. EC2 boots a Deep Learning AMI with NVIDIA drivers
2. `gpu-start.sh` (in user-data) installs Tailscale, joins the tailnet,
   docker-pulls the engine image from GHCR
3. The engine container starts, mounts `/data/models` for the on-disk cache
4. The engine downloads the model (S3-first, HuggingFace fallback). Once
   `/data` is populated, restarts skip this step.
5. The container's `/health` endpoint flips to `ok`

Tail the bootstrap log if something looks off:

```bash
dalston-aws ssh --name faster-whisper
sudo cat /var/log/user-data.log
```

---

## Talking to the engine

The container exposes a Dalston-native HTTP API on port 9100. The simplest
shape: a multipart POST.

```bash
curl -X POST http://dalston-engine-faster-whisper:9100/v1/transcribe \
  -F file=@meeting.wav \
  -F language=en
```

Or from the SDK by pointing it at the engine directly:

```python
from dalston_sdk import Dalston

# Single-engine mode: base_url is the engine, not a control plane.
client = Dalston(base_url="http://dalston-engine-faster-whisper:9100")
job = client.transcribe("meeting.wav", language="en")
print(job.transcript.text)
```

> **No control plane = no DAG.** In this mode the engine handles one stage at
> a time. If you want PREPARE → TRANSCRIBE → ALIGN → DIARIZE → MERGE
> assembled into a single transcript with speakers, run two engines and stitch
> the outputs yourself, or use the full control plane (Path B) instead.

For diarization + transcription on the same instance with one container, look
at the **combo engine** `hf-asr-align-pyannote` — it does all three stages in a
single Python process. See
[32-diarization-vs-transcription.md](32-diarization-vs-transcription.md).

---

## Tearing it down

```bash
dalston-aws engine down
# Cancels the spot request and terminates the instance.
```

This terminates the instance and (for spot) cancels the spot request. The
`/data` EBS attached to that single-engine box is **deleted with the instance**
— this mode is intentionally ephemeral. The persistent EBS volume lives on the
control plane in split mode, not on per-engine workers. Cache rebuilds on the
next launch (~30 seconds for ONNX, ~3 minutes for Whisper / Parakeet, ~5
minutes for Voxtral).

If you need the model cache to persist across reclaims, use split mode:
[21-control-plane-aws-deploy.md](21-control-plane-aws-deploy.md).

---

## Common gotchas

- **`Engine 'X' is already deployed`** — you have a stale state file from a
  previous run that didn't terminate cleanly. Run `dalston-aws engine down`,
  or delete `~/.dalston/aws-engine-state.json` if the instance is already
  gone.
- **Hostname doesn't resolve** — Tailscale MagicDNS hasn't caught up yet.
  Wait 30 seconds, or use the IP shown in `engine status`.
- **HF_TOKEN missing for pyannote** — set it in your shell before
  `engine up`; the script forwards it via user-data. The model is gated and
  will refuse to load without a token.

---

## What this mode is **not**

- Not multi-tenant — there's no API key system, no rate limits, no audit log
- Not durable — no Postgres, no job records, no webhooks
- Not high-availability — one box, one engine
- Not the right shape for real-time WebSocket streaming with the
  ElevenLabs/OpenAI compatibility layer (those live in the gateway). For
  real-time, use the full control plane.

For all of those, see [21-control-plane-aws-deploy.md](21-control-plane-aws-deploy.md).
