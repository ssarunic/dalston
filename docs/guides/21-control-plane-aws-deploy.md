# Deploying the control plane on AWS

> Twenty minutes from `aws sts get-caller-identity` to a working,
> ElevenLabs/OpenAI-compatible STT API at `https://dalston-control-plane.<your-tailnet>.ts.net`.
> No public ports. Real Let's Encrypt cert. Total cost: ~$87/month split mode.

This is the production-grade path. If you only want a one-shot GPU for the
afternoon, use [11-single-engine-tailscale-mode.md](11-single-engine-tailscale-mode.md)
instead.

> **Engineering reference:** [aws-deploy.md](aws-deploy.md) is the deeper
> doc. This page is the sales-friendly tutorial.

---

## What you're about to deploy

```
Control plane (always-on, on-demand t3.large)
  • Gateway       (FastAPI on :8000 → :443 via tailscale serve)
  • Orchestrator  (DAG scheduler, webhook delivery)
  • Postgres      (jobs, API keys, audit log)
  • Redis         (engine registry, event stream, task queues)
  • Web console   (React SPA, served by gateway)
  • CPU engines   (prepare, redact, merge, llm-cleanup)

GPU worker (ephemeral, spot g6.xlarge by default)
  • Whatever transcribe/diarize engines you launch
  • Joins the same Tailscale tailnet, talks to control plane Redis
```

Storage:

- Postgres + Redis + CPU-engine model cache → 50 GB EBS `/data` on the control plane
- GPU-worker model cache → worker-local `/data/models`; pre-stage S3 for fast replacement workers
- Audio + transcripts → S3 bucket created during `setup`

---

## Prerequisites

```bash
aws sts get-caller-identity   # Confirms your AWS CLI is configured
tailscale status              # Tailscale running and authenticated on your laptop
```

Put `dalston-aws` on your `PATH`:

```bash
ln -s "$(pwd)/infra/scripts/dalston-aws" /usr/local/bin/dalston-aws
```

Pre-flight: enable MagicDNS HTTPS in your tailnet at
<https://login.tailscale.com/admin/dns> (toggle MagicDNS on, toggle HTTPS
Certificates on). One-time, tailnet-wide.

---

## The 4 commands

```bash
# 1. Provision (S3 bucket, IAM role, security group, keypair).
#    Idempotent: re-running doesn't re-create anything.
dalston-aws setup -t split

# 2. Boot both instances (control plane + GPU worker).
dalston-aws launch

# 3. Wait until ready (~3-5 min for first launch with model downloads).
dalston-aws status

# 4. Find your URL.
dalston-aws ssh -- 'tailscale status --json | python3 -c "import sys,json;print(json.load(sys.stdin)[\"Self\"][\"DNSName\"])"'
# → dalston-control-plane.<your-tailnet>.ts.net
```

That's it. Open the URL in any browser on your tailnet:

- `https://dalston-control-plane.<your-tailnet>.ts.net/` → web console
- `https://dalston-control-plane.<your-tailnet>.ts.net/v1/audio/transcriptions` → REST
- `wss://dalston-control-plane.<your-tailnet>.ts.net/v1/audio/transcriptions/stream` → real-time

Real Let's Encrypt cert, no warnings. WebSocket upgrades work. `getUserMedia`
works (it's a true secure context, so the browser allows mic capture).

---

## First API call

`dalston-aws setup` generated an admin API key, stored it in
`~/.dalston/aws-state.yaml`, and `dalston-aws launch` seeded it as
`DALSTON_API_KEY` on the control plane.

```bash
# Pull the API key from local deployment state.
API_KEY=$(awk -F': ' '/^api_key:/ {print $2}' ~/.dalston/aws-state.yaml)
URL="https://dalston-control-plane.<your-tailnet>.ts.net"
export DALSTON_SERVER="$URL"
export DALSTON_API_KEY="$API_KEY"

curl -X POST $URL/v1/audio/transcriptions \
  -H "Authorization: Bearer $API_KEY" \
  -F file=@meeting.mp3 \
  -F speaker_detection=diarize \
  -F timestamps_granularity=word
```

If your state file predates seeded keys and has no `api_key:` line, SSH to the
control plane and read the gateway first-run log, then mint a fresh key from
the web console.

Or with the SDK:

```python
import os
from dalston_sdk import Dalston

client = Dalston(
    base_url=os.environ["DALSTON_SERVER"],
    api_key=os.environ["DALSTON_API_KEY"],
)
job = client.transcribe(
    "meeting.mp3",
    speaker_detection="diarize",
    timestamps_granularity="word",
)
job = client.wait_for_completion(job.id)
print(job.transcript.text)
```

---

## Day-to-day commands

```bash
dalston-aws status        # what's running, how much it's costing
dalston-aws ssh           # SSH to control plane via Tailscale
dalston-aws ssh gpu       # SSH to GPU worker
dalston-aws down          # stop control plane (EBS preserved, ~$4/mo)
dalston-aws up            # bring it back
dalston-aws up --pull     # pull latest GHCR images on boot, redeploy
```

For GPU workers specifically:

```bash
dalston-aws launch gpu --engines onnx --spot
dalston-aws launch gpu --engines pyannote,nemo --spot     # co-located
dalston-aws launch gpu --engines vllm-asr --on-demand     # stable Voxtral box
dalston-aws terminate gpu --name onnx                     # remove one worker
dalston-aws reconcile     # re-resolve Tailscale hostnames if needed
```

Logs:

```bash
dalston-aws ssh
cd /data/dalston
docker compose -f docker-compose.yml -f infra/docker/docker-compose.aws.yml \
  --env-file .env.aws logs -f gateway
```

---

## Cost on this setup

| State | Hourly | Monthly |
|---|---|---|
| Both running | ~$0.12/hr | ~$87 |
| Control plane only (GPU torn down) | ~$0.08/hr | ~$60 |
| Both stopped (`dalston-aws down`) | ~$0.005/hr (EBS only) | ~$8 |

Cut your bill by half: `dalston-aws down` overnight. Bring back up with
`dalston-aws up` in the morning. The control-plane database and artifacts
survive on EBS/S3; GPU workers are replaceable and reload models from S3 or
HuggingFace.

Full breakdown: [51-aws-cost-estimator.md](51-aws-cost-estimator.md).

---

## When the GPU spot worker is reclaimed

It will happen eventually. The control plane keeps running. The orchestrator
re-queues in-flight tasks. Recovery:

```bash
dalston-aws launch gpu --engines <your-engines> --spot
```

Full story: [13-spot-interruptions-recovery.md](13-spot-interruptions-recovery.md).

---

## Tearing it all down

```bash
dalston-aws teardown
```

Terminates instances, deletes EBS volumes, security groups, IAM role.
**Does not** delete S3 bucket (it has your transcripts) — the script prints
the AWS CLI command to do that yourself if you want to.

---

## Verifying it works

After `launch`, on your laptop:

```bash
URL="https://dalston-control-plane.<your-tailnet>.ts.net"
curl $URL/health
# → {"status":"healthy",...}

curl -H "Authorization: Bearer $API_KEY" $URL/v1/engines
# → list of running engines with their status, capacity, models loaded
```

Then open the web console at `$URL/` and confirm:

- Dashboard shows green
- Engines page lists your transcribe + diarize workers
- Settings → API keys page lets you mint additional keys

---

## See also

- [aws-deploy.md](aws-deploy.md) — full engineering reference (HTTPS, cert
  lifecycle, idempotency, troubleshooting)
- [aws-deployment-scenarios.md](aws-deployment-scenarios.md) — instance type tradeoffs
- [20-control-plane-tour.md](20-control-plane-tour.md) — what each component does
- [22-using-the-web-console.md](22-using-the-web-console.md)
- [23-using-the-cli.md](23-using-the-cli.md)
- [24-using-the-python-sdk.md](24-using-the-python-sdk.md)
- [13-spot-interruptions-recovery.md](13-spot-interruptions-recovery.md)
