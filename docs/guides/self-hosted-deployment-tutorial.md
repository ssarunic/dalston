# Self-hosting Dalston with Docker Compose

This guide runs the current Dalston control plane and engines from this
repository. It uses local PostgreSQL, Redis, and MinIO containers and does not
provision cloud infrastructure.

## Prerequisites

- Docker Engine or Docker Desktop with Compose v2 (`docker compose version`);
- Git;
- enough disk for container images and model caches; and
- Python 3.11+ only if you also want to run repository tests or local tools.

For NVIDIA acceleration, install a working NVIDIA driver and NVIDIA Container
Toolkit. Configure Docker with the toolkit’s current runtime command:

```bash
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker
nvidia-smi
docker info | grep -i runtimes
```

The command is `nvidia-ctk runtime configure`; `engine_id` is not a valid
subcommand or option.

## Configure

From the repository root:

```bash
cp .env.example .env
```

The checked-in defaults use local MinIO credentials and are for local
self-hosting only. Do not reuse them for an internet-facing deployment.

Set `HF_TOKEN` in `.env` when running gated pyannote models. You must also have
accepted the applicable model terms on Hugging Face.

To seed a stable local admin key on first boot, set:

```dotenv
DALSTON_API_KEY=dk_local_dev_change_me
```

If it is omitted, the gateway generates an admin key once and prints it in its
startup log.

## Start the stack

The Make targets are the supported shortcuts:

```bash
make dev-minimal
```

`dev-minimal` starts local infrastructure, the gateway, orchestrator, audio
preparation, faster-whisper transcription, and phoneme alignment. Use the full
CPU-oriented stack when you need the other default engines:

```bash
make dev
```

For NVIDIA engines, use the GPU override and profiles:

```bash
make dev-gpu
```

Do not run local Python gateway/orchestrator processes at the same time as
their Docker containers. The Make targets call `make clean-local` before
starting the stack.

## Verify

```bash
make ps
make health
curl http://127.0.0.1:8000/health
```

If the gateway generated an API key, retrieve it from its startup output:

```bash
docker compose logs gateway
```

Then submit a file:

```bash
export DALSTON_API_KEY=dk_...

curl -X POST http://127.0.0.1:8000/v1/audio/transcriptions \
  -H "Authorization: Bearer $DALSTON_API_KEY" \
  -F file=@meeting.wav
```

Open <http://127.0.0.1:8000/> for the web console.

## Choose services accurately

Current Compose service names include:

| Purpose | Service |
| --- | --- |
| Gateway | `gateway` |
| Orchestrator | `orchestrator` |
| Preparation | `stt-prepare` |
| Faster Whisper | `stt-transcribe-faster-whisper` |
| ONNX | `stt-transcribe-onnx` |
| NeMo | `stt-transcribe-nemo` |
| Alignment | `stt-align-phoneme` |
| Pyannote | `stt-diarize-pyannote-4.0` |
| PII detection | `stt-pii-detect-presidio` |
| Audio redaction | `stt-audio-redact-audio` |

Inspect the authoritative list rather than copying old service names:

```bash
docker compose config --services
docker compose config --profiles
```

For example, rebuild one current engine with:

```bash
make rebuild ENGINE=stt-transcribe-faster-whisper
```

There is no `stt-transcribe-whisper-cpu`,
`stt-diarize-pyannote-v40-cpu`, or `whisperx-align` service.

## Observability

Start the optional local observability profile:

```bash
make dev-observability
```

It adds Jaeger, Prometheus, Grafana, Loki, and the metrics exporter. Default
local ports are defined in `docker-compose.yml`; notable UIs are:

- Grafana: <http://127.0.0.1:3001/>
- Prometheus: <http://127.0.0.1:9090/>
- Jaeger: <http://127.0.0.1:16686/>

## Operations

```bash
make logs
make logs-all
make stop
make validate
```

Container logs are capped by the Compose logging configuration. PostgreSQL,
Redis, MinIO, and model data live in named Docker volumes.

Back up PostgreSQL without stopping the stack:

```bash
docker compose exec -T postgres \
  pg_dump -U dalston dalston > dalston-backup.sql
```

Restore into a compatible empty database:

```bash
docker compose exec -T postgres \
  psql -U dalston dalston < dalston-backup.sql
```

`make clean-all` removes containers, images, and volumes, including the
database. It is intentionally destructive; ordinary shutdown should use
`make stop`.

## Troubleshooting

### A service never becomes healthy

```bash
docker compose ps
docker compose logs gateway orchestrator
docker compose logs stt-prepare stt-transcribe-faster-whisper
```

### GPU containers cannot see CUDA

Verify `nvidia-smi` on the host, rerun the toolkit configuration command from
the prerequisites, and validate the CUDA test container before restarting
Dalston.

### A job remains queued

Check registered engines and their streams:

```bash
curl -H "Authorization: Bearer $DALSTON_API_KEY" \
  http://127.0.0.1:8000/v1/engines
docker compose exec -T redis redis-cli KEYS "dalston:stream:*"
```

Engine streams use the selected engine ID, for example
`dalston:stream:faster-whisper`.

### Model download fails

Check the engine log, outbound HTTPS access, `DALSTON_MODEL_SOURCE`, and
`HF_TOKEN` where required. Local mode defaults to `auto`, which can fall back
to Hugging Face when MinIO has no cached model.

## Production boundary

This Compose workflow is suitable for a private server or development
environment. Before exposing it beyond a trusted network, provide TLS, durable
backups, secret management, restricted ingress, monitoring, and an upgrade
process. For the repository’s AWS/Tailscale automation, see
[Deploying Dalston on AWS](aws-deploy.md).
