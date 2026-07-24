# Quickstart — your first transcript in 5 minutes

> Self-host an ElevenLabs- and OpenAI-compatible speech-to-text API.
> Run on your laptop or your own cloud account. You own the models, cache, and
> bill. See the dated [AWS cost estimator](51-aws-cost-estimator.md) instead of
> relying on a copied headline price.

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

> **Why CPU?** Your laptop probably does not have an NVIDIA GPU. The CPU stack
> favors compatibility over throughput. See
> [12-engine-presets-catalog.md](12-engine-presets-catalog.md) and measure the
> selected model on your hardware before estimating completion time.

---

## Path A — `curl` (no installs)

```bash
curl -X POST http://localhost:8000/v1/audio/transcriptions \
  -H "Authorization: Bearer $DALSTON_API_KEY" \
  -F file=@meeting.mp3 \
  -F model=auto \
  -F language=auto
# → { "id": "550e8400-e29b-41d4-a716-446655440000", "status": "pending", ... }
```

Poll until done:

```bash
curl http://localhost:8000/v1/audio/transcriptions/550e8400-e29b-41d4-a716-446655440000 \
  -H "Authorization: Bearer $DALSTON_API_KEY"
# → { "status": "completed", "text": "...", "segments": [...], ... }
```

That's it. Same shape as ElevenLabs / OpenAI; drop-in friendly.

---

## Read results defensively

Model capabilities differ, so optional result fields are not universal:

- `language_code` can be `und` and language confidence can be `null` when
  detection is inconclusive.
- Segment/word confidence can be `null`.
- Asking for word timestamps does not manufacture them; an engine without that
  capability returns the timing it has and a warning.
- Warnings can also report low speech coverage. The optional
  `DALSTON_PREPARE_SPEECH_REGIONS=1` diagnostic adds speech-region analysis; it
  is off by default.
- Audio metadata describes the original upload even when the prepare stage
  creates normalized mono/16 kHz working audio.

Check `warnings` and test the selected model before making word timing or
confidence mandatory in an application.

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
JOB_ID=$(dalston transcribe meeting.mp3 --speakers diarize --no-wait --json | jq -r '.id')
dalston jobs wait "$JOB_ID"
dalston export "$JOB_ID" --format srt -o out.srt
dalston transcribe meeting.mp3 --model faster-whisper --language en --show-words
dalston listen   # real-time microphone capture
dalston status   # health check
dalston jobs list
```

The CLI reads `DALSTON_SERVER` and `DALSTON_API_KEY` from the environment or
`~/.dalston/config.yaml`. Installing `dalston-cli` gives you the client; its
automatic localhost bootstrap additionally needs the Dalston backend
dependencies. In this quickstart, `make dev` already provides the server. See
[23-using-the-cli.md](23-using-the-cli.md).

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
