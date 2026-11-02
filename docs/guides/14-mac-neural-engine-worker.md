# Mac Neural Engine worker

Attach an Apple Silicon Mac on your tailnet to a Dalston control plane as a
batch worker. Parakeet transcription and pyannote diarization run as Core ML
models on the Neural Engine through [FluidAudio](https://github.com/FluidInference/FluidAudio),
in a Swift sidecar; two small Python adapters make it look like any other
engine to the orchestrator. Spec: `docs/plan/milestones/M104-fluidaudio-mac-worker.md`.

## What you get

- Two engines on the Infrastructure page under a **Mac · Neural Engine**
  badge: `fluidaudio` (transcribe, Parakeet TDT 0.6B v3 or Parakeet Ultra)
  and `fluidaudio-pyannote` (diarize, pyannote community-1 + VBx).
- Jobs route to them like any other engine: pin a model
  (`model=fluidaudio/parakeet-tdt-0.6b-v3`,
  `model_diarize=fluidaudio/speaker-diarization`) or let the selector pick.
- No torch, no CUDA, no Docker on the Mac. The adapters are plain Python in
  a venv; inference is one native process.

What you do not get: realtime (WebSocket) sessions, autoscaling, or any
"prefer the Mac" routing rule. The Mac is on when its launchd agents run and
off when they do not.

## Prerequisites

- Apple Silicon Mac, macOS 15 or newer, Xcode command line tools
  (`xcode-select --install`), Python 3.11+ (`brew install python@3.12`).
- Tailscale on the Mac, joined to the same tailnet as the control plane.
  `tailscale ping dalston-control-plane` must answer.
- The control plane's Redis and object store reachable from the tailnet.
  The AWS deployment already binds Redis on the Tailscale IP for GPU
  workers; a `make dev` control plane needs `DALSTON_BIND_IP` set to its
  Tailscale IP so Redis (6379) and MinIO (9000) are reachable.
- An IAM user (or MinIO key) scoped to the artifact bucket only, as a
  named profile in `~/.aws/credentials`. Never the account admin key.

## Install

From a checkout of this repository on the Mac:

```bash
bash engines/stt-transcribe/fluidaudio/mac/install.sh
```

The first run builds `fluidaudio-server` (fetches FluidAudio and Vapor),
creates `~/.dalston/fluidaudio/venv`, seeds `~/.dalston/fluidaudio/worker.env`
from the example and stops. Edit that file:

| Key | Set to |
|---|---|
| `REDIS_URL` | `redis://dalston-control-plane:6379` (MagicDNS name or Tailscale IP) |
| `DALSTON_S3_BUCKET`, `DALSTON_S3_REGION`, `AWS_REGION` | the control plane's artifact bucket |
| `DALSTON_S3_ENDPOINT_URL` | only for a `make dev` control plane: `http://<cp-tailscale-ip>:9000` |
| `AWS_PROFILE` | the bucket-scoped profile |
| `DALSTON_WORKER_ID`, `DALSTON_HOST_HOSTNAME` | a short name for this Mac (the SDK keeps 12 characters) |

Run the installer again. It renders three launchd agents into
`~/Library/LaunchAgents` and starts them:

| Agent | Runs |
|---|---|
| `com.dalston.fluidaudio-server` | the Swift sidecar on `127.0.0.1:9200`, under `caffeinate -i` |
| `com.dalston.fluidaudio-transcribe` | the `fluidaudio` adapter, metrics on 9100 |
| `com.dalston.fluidaudio-diarize` | the `fluidaudio-pyannote` adapter, metrics on 9101 |

Logs: `~/Library/Logs/dalston/fluidaudio-*.log`. The server's first start
downloads and compiles the Core ML models (a few minutes); the adapters
report `unhealthy` until `/health` answers.

## Make the models routable

The orchestrator routes only to models whose registry row is `ready`. On
the control plane's Models page (or with `POST /v1/models/{id}/pull`) pull:

- `fluidaudio/parakeet-tdt-0.6b-v3`
- `fluidaudio/parakeet-ultra`
- `fluidaudio/speaker-diarization`

This mirrors each Hugging Face repo into the artifact bucket. The Mac does
not read that copy; FluidAudio downloads the same repo itself. It is a known
wart (spec, Open Question 1) and the price of v1 needing no registry change.

## Verify

```bash
# On the Mac: the sidecar
curl -s http://127.0.0.1:9200/health | jq .
curl -s -F file=@tests/fixtures/test_audio.wav http://127.0.0.1:9200/v1/transcribe | jq '.words | length'

# On the Mac: an adapter without Redis or S3 (server must be up)
echo '{"language":"en","word_timestamps":true}' > /tmp/fluid-config.json
~/.dalston/fluidaudio/venv/bin/python -m dalston.engine_sdk.local_runner run \
  --engine engines/stt-transcribe/fluidaudio/engine.py:FluidAudioBatchEngine \
  --stage transcribe --audio tests/fixtures/test_audio.wav \
  --config /tmp/fluid-config.json --output /tmp/fluid-transcribe.json
jq '.data.segments[0].words[0]' /tmp/fluid-transcribe.json

# From anywhere on the tailnet: both engines heartbeating
curl -s -H "Authorization: Bearer $DALSTON_API_KEY" http://dalston-control-plane:8000/v1/engines \
  | jq '.engines[] | select(.id|startswith("fluidaudio")) | {id, status, loaded_model}'
# status "running" = heartbeating. "available" = in the catalog, no heartbeat.

# End to end
curl -s -X POST http://dalston-control-plane:8000/v1/audio/transcriptions \
  -H "Authorization: Bearer $DALSTON_API_KEY" \
  -F file=@meeting.wav -F model=fluidaudio/parakeet-tdt-0.6b-v3 \
  -F speaker_detection=diarize -F model_diarize=fluidaudio/speaker-diarization | jq '.id'
```

## Sleep, disconnects and routing

Be clear-eyed about what a laptop is:

- **Heartbeat TTL is 60 s.** When the Mac sleeps or leaves the tailnet, the
  registry forgets its engines within a minute.
- **In-flight tasks wait for the Mac.** Stale-task reclaim hands work to
  *another instance of the same engine*. With one Mac there is none, so a
  task it had claimed sits until the Mac wakes or the task times out
  (3 × catalog RTF × audio duration).
- **Pinned jobs fail fast while it is asleep.** A job that names a
  `fluidaudio/*` model with no heartbeating engine fails with
  `runtime_unavailable`. External engines are never `on_demand`, so nothing
  queues them for an autoscaler that does not exist.
- **Auto-routed jobs go elsewhere.** The selector only considers healthy
  engines.
- **When both are online**, the selector ranks word-timestamp engines by the
  catalog `rtf_gpu`. The FluidAudio entries ship with a deliberately
  pessimistic 0.3 so GPU workers win by default; replace it with measured
  numbers once you have them (spec step 104.5).
- `caffeinate -i` stops idle sleep, not lid-close sleep. Run the Mac with
  the lid open or `sudo pmset -a disablesleep 1`, and stop the agents before
  travelling.

## Stop, start, uninstall

```bash
uid=$(id -u)
launchctl bootout gui/$uid/com.dalston.fluidaudio-transcribe
launchctl bootout gui/$uid/com.dalston.fluidaudio-diarize
launchctl bootout gui/$uid/com.dalston.fluidaudio-server
launchctl bootstrap gui/$uid ~/Library/LaunchAgents/com.dalston.fluidaudio-server.plist   # etc.

bash engines/stt-transcribe/fluidaudio/mac/install.sh --uninstall   # agents only; venv, binary and models stay
```

Stop the adapters first so the registry sees a clean `offline`, then the
server.

## Security notes

- The sidecar binds loopback only. Nothing on the tailnet can reach it.
- Redis on the control plane is unauthenticated; it is protected by the
  tailnet and the AWS security groups, exactly as for GPU workers. Tag the
  Mac in your Tailscale ACLs the same way.
- The bucket-scoped IAM key lives in `~/.aws/credentials` on a laptop.
  Rotate it like any other long-lived key; keychain storage is a follow-up.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| Adapter log: `server_capabilities_unavailable` / health `unhealthy` | sidecar still compiling models; `tail -f ~/Library/Logs/dalston/fluidaudio-server.log` |
| `/v1/engines` shows `available`, not `running` | adapter cannot reach Redis: check `REDIS_URL`, `tailscale status`, and that the control plane binds Redis on its Tailscale IP |
| Task fails with `NoCredentialsError` / `AccessDenied` | `AWS_PROFILE` missing or not scoped to the bucket; for `make dev`, `DALSTON_S3_ENDPOINT_URL` unset |
| Job fails with `runtime_unavailable` | Mac asleep or adapters stopped; pinned model, no heartbeat |
| Job fails with `model_not_ready` | pull the three `fluidaudio/*` models on the Models page |
| Second adapter has no `/health` | both adapters on one metrics port; `run-adapter.sh` sets 9100/9101, so a hand-set `DALSTON_METRICS_PORT` in `worker.env` is the usual culprit |
| 503 from the sidecar | its queue is full; something is retrying when it should not. Check for a stray adapter process. |

## See also

- [02-pick-your-deployment.md](02-pick-your-deployment.md) — where this fits
- [13-spot-interruptions-recovery.md](13-spot-interruptions-recovery.md) — the same questions for EC2 workers
- `engines/stt-transcribe/fluidaudio/server/README.md` — the sidecar's HTTP contract
