# Quickstart — your first transcript in 5 minutes

> Self-host an ElevenLabs- and OpenAI-compatible speech-to-text API.
> **$0** on your laptop. **~$0.20/hr** on a rented spot GPU when you need one.
> **~$87/month** all-in for a 24/7 service. You own the models, the cache,
> and the bill.

This page gets you from zero to a finished transcript through three different
front doors. Pick the one that matches how you already work — they all hit the
same backend.

---

## Before you start

You need:

- **Docker** with Compose v2
- **Python 3.11+** (only if you want the SDK or CLI)
- An audio file to transcribe — anything `ffmpeg` can read (mp3, wav, m4a, opus, mp4, …)

Clone the repo and bring up the local stack:

```bash
git clone https://github.com/ssarunic/dalston.git
cd dalston

# Seed a stable local admin key before the first gateway boot.
cp .env.example .env
printf '\nDALSTON_API_KEY=dk_local_dev_only_change_me\n' >> .env
printf 'DALSTON_MODEL_SOURCE=auto\n' >> .env
export DALSTON_API_KEY=$(grep '^DALSTON_API_KEY=' .env | cut -d= -f2-)

make dev
```

`make dev` starts Postgres, Redis, MinIO, the gateway (port 8000), the
orchestrator, and a CPU-only engine set. First boot takes **~2–3 minutes**
(model download is the bottleneck — see
[30-how-models-are-fetched.md](30-how-models-are-fetched.md)); subsequent
boots are seconds. Wait until `make health` reports green, then confirm the
API key is in your shell:

```bash
echo "$DALSTON_API_KEY"
# dk_local_dev_only_change_me
```

If you already ran `make dev` without seeding a key, the gateway generated one
and printed it once. Grab it from the logs, then export it:

```bash
docker compose logs gateway | sed -n 's/.*API Key: //p' | tail -1
export DALSTON_API_KEY=dk_...
```

> **Why CPU?** Your laptop probably doesn't have an NVIDIA GPU. The CPU stack
> uses faster-whisper at RTF 0.4 — a 1-hour podcast finishes in ~24 minutes
> on a modern CPU. On a GPU, faster-whisper at RTF 0.03 finishes the same hour
> in ~108 seconds (~13× faster); NeMo can be much faster for English. See
> [12-engine-presets-catalog.md](12-engine-presets-catalog.md).

---

## Path A — `curl` (no installs)

```bash
curl -X POST http://localhost:8000/v1/audio/transcriptions \
  -H "Authorization: Bearer $DALSTON_API_KEY" \
  -F file=@meeting.mp3 \
  -F model=auto \
  -F language=auto
# → { "id": "job_abc123", "status": "pending", ... }
```

Poll until done (a 5-minute audio file takes **~2 minutes** of CPU model time
at RTF 0.4 — a 1-hour file takes **~24 minutes**):

```bash
curl http://localhost:8000/v1/audio/transcriptions/job_abc123 \
  -H "Authorization: Bearer $DALSTON_API_KEY"
# → { "status": "completed", "transcript": { "text": "..." } }
```

That's it. Same shape as ElevenLabs / OpenAI; drop-in friendly.

---

## Path B — Python SDK

```bash
pip install -e ./sdk
```

```python
import os
from dalston_sdk import Dalston

client = Dalston(
    base_url="http://localhost:8000",
    api_key=os.environ["DALSTON_API_KEY"],
)

job = client.transcribe("meeting.mp3", language="auto")
job = client.wait_for_completion(job.id)

print(job.transcript.text)
```

Want speakers? One flag:

```python
job = client.transcribe(
    "meeting.mp3",
    speaker_detection="diarize",   # adds pyannote speaker turns
    timestamps_granularity="word", # word-level timing
)
```

See [24-using-the-python-sdk.md](24-using-the-python-sdk.md) for async, real-time, webhooks.

---

## Path C — `dalston` CLI

```bash
pip install -e ./cli
```

```bash
dalston transcribe meeting.mp3
# Streams the transcript to stdout when done.
```

Common knobs:

```bash
dalston transcribe meeting.mp3 --speakers diarize --format srt -o out.srt
dalston transcribe meeting.mp3 --model faster-whisper --language en --show-words
dalston listen   # real-time microphone capture
dalston status   # health check
dalston jobs list
```

The CLI reads `DALSTON_SERVER` and `DALSTON_API_KEY` from the env, or
`~/.dalston/config.yaml`. See [23-using-the-cli.md](23-using-the-cli.md).

---

## Where do I go from here?

| You want… | Read |
|---|---|
| A GPU for one afternoon, billed by the second | [11-single-engine-tailscale-mode.md](11-single-engine-tailscale-mode.md) |
| 24/7 ElevenLabs-compatible API on AWS | [21-control-plane-aws-deploy.md](21-control-plane-aws-deploy.md) |
| Pick the right deployment | [02-pick-your-deployment.md](02-pick-your-deployment.md) |
| Real-time streaming (WebSocket) | [40-realtime-overview.md](40-realtime-overview.md) |
| What it'll cost | [51-aws-cost-estimator.md](51-aws-cost-estimator.md) |
| How models load, how stages work | [30-how-models-are-fetched.md](30-how-models-are-fetched.md) |
