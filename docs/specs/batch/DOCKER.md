# Docker Compose reference

Dalston's development composition is defined by `docker-compose.yml`.
`docker compose config` is the source of truth after profiles and environment
substitution have been applied.

For normal local workflows, prefer the Make targets:

```bash
make dev
make ps
make health
make logs
make stop
```

`make dev-minimal` starts the smallest useful stack, while `make dev-gpu`
enables the GPU-oriented profile. Run `make validate` after changing Compose
files.

## Current services

The base composition contains:

- Infrastructure: `postgres`, `redis`, `minio`, and one-shot `minio-init`.
- Control plane: `gateway` and `orchestrator`.
- CPU-capable engines: `stt-prepare`, `stt-transcribe-onnx`,
  `stt-transcribe-faster-whisper`, `stt-diarize-nemo-sortformer-cpu`,
  `stt-pii-detect-presidio`, and `stt-audio-redact-audio`.
- Optional/profile engines: `stt-align-phoneme`,
  `stt-diarize-pyannote-4.0`, `stt-transcribe-hf-asr`,
  `stt-transcribe-nemo`, and `stt-combo-whisper-align-pyannote`.

Get the effective list rather than copying it from this document:

```bash
docker compose config --services
```

There is no standalone session-router container. Realtime worker allocation is
coordinated by the gateway through the embedded `SessionCoordinator`. There is
also no queue-backed merge service in the current pipeline; final assembly is
orchestrator completion logic.

## Images and Dockerfiles

Control-plane images use:

- `docker/Dockerfile.gateway`
- `docker/Dockerfile.orchestrator`

Engine base images live under `docker/Dockerfile.base-*`. Each authored engine
has its own `Dockerfile` and `engine.yaml` under `engines/`. Consult
`docker-compose.yml` for the exact build context, target, profile, device
reservation, and health check.

## Storage

The local composition persists:

- PostgreSQL data in `postgres-data`.
- MinIO objects in `minio-data`.
- downloaded models in the shared `model-cache` volume mounted at `/models`.

Redis is operational coordination state, not the durable source for completed
jobs. Do not treat its volume as a database backup.

Removing named volumes destroys local state. `make stop` preserves volumes;
inspect the exact command before using any workflow that includes `down -v`.

## Scaling and logs

Scale a stateless engine by its exact current Compose service name:

```bash
docker compose up -d --scale stt-transcribe-faster-whisper=2
docker compose logs -f stt-transcribe-faster-whisper
```

Do not scale PostgreSQL, Redis, MinIO, the gateway, or orchestrator this way
without an architecture designed for those replicas.

Engine registration and task consumption can be checked with:

```bash
docker compose ps
docker compose logs orchestrator
docker compose logs stt-transcribe-faster-whisper
docker compose exec redis redis-cli XINFO GROUPS dalston:tasks:transcribe
```

The exact stream names are defined in `dalston/common/streams.py`.

## GPU operation

GPU services require a working NVIDIA driver, Docker GPU support, and compatible
host hardware. Validate the host first:

```bash
nvidia-smi
docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi
```

Use the compose profiles and device reservations already defined in the
repository. Engine memory requirements and benchmark RTF values are planning
inputs, not capacity guarantees; record hardware, software version, model, and
test date when publishing measurements.

## Configuration

Copy `.env.example` to `.env` for local development. The main categories are:

- `DATABASE_URL`, `POSTGRES_PASSWORD`
- `REDIS_URL`
- `DALSTON_S3_*` and AWS credentials for MinIO/S3
- `DALSTON_API_KEY` for a stable local admin key
- `DALSTON_MODEL_SOURCE` and optional `HF_TOKEN`
- `DALSTON_RATE_LIMIT_*`
- `DALSTON_ENGINE_UNAVAILABLE_BEHAVIOR` and
  `DALSTON_ENGINE_WAIT_TIMEOUT_SECONDS`

See [Configuration reference](../../reference/configuration.md) for values
owned by application settings. Never reuse the example MinIO credentials for
AWS.

## Health and troubleshooting

Start with:

```bash
make ps
make health
make logs-all
docker compose config
```

Then narrow the problem:

- A gateway/orchestrator failure usually points to PostgreSQL, Redis,
  migrations, or configuration.
- An engine that is healthy but idle may have no compatible task, model, or
  stream assignment.
- A model download failure may require `HF_TOKEN`, license acceptance, or
  writable `/models`.
- GPU allocation failures should be reproduced with the NVIDIA smoke test
  before changing Dalston configuration.

Do not run local Python and Docker copies of the gateway or orchestrator at the
same time; duplicate consumers make queue behavior misleading.
