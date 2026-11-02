# M104: FluidAudio Mac Worker (Neural Engine Sidecar)

|                    |                                                                                                                                      |
| ------------------ | ------------------------------------------------------------------------------------------------------------------------------------ |
| **Goal**           | Run an Apple Silicon Mac as a Dalston batch worker, with Parakeet and pyannote inference on the Neural Engine via FluidAudio          |
| **Duration**       | 5–7 days                                                                                                                             |
| **Dependencies**   | M70 (Riva NIM Engine Integration — sidecar reference), M79 (Leaf Engine HTTP API), M78 (Infrastructure Topology View, node identity fields) |
| **Deliverable**    | `fluidaudio-server` Swift executable, `fluidaudio` transcribe adapter, `fluidaudio-pyannote` diarize adapter, `deployment: external` catalog flag, model catalog entries, Mac worker install (launchd + env), operator guide |
| **Status**         | Not Started                                                                                                                          |

## User Story

> *"As an operator with a Mac on my tailnet, I want to attach it to my Dalston control plane as a worker — the same way I launch a spot GPU box — so that batch transcription and diarization run on the Mac's Neural Engine at no marginal cost, using the same Core ML models that dalston-mobile already runs."*

---

## Outcomes

| Scenario | Current | After M104 |
| -------- | ------- | ---------- |
| Adding compute to a control plane | Only EC2 GPU workers via `dalston-aws launch gpu` | A Mac on the tailnet registers as a worker with two engines; the orchestrator routes to it like any other |
| Parakeet on a Mac | NeMo engine is CUDA-only; ONNX engine runs on CoreML/CPU via Python | Parakeet TDT 0.6B v3 / Ultra run as Core ML models on the Neural Engine via FluidAudio |
| pyannote on a Mac | Docker image is `linux/amd64` only (torchcodec); native Python needs torch + MPS | pyannote community-1 + VBx runs as Core ML via FluidAudio, no torch on the Mac |
| Engine process footprint | Every engine is a Python process that imports its ML stack | Inference lives in one Swift process; the Dalston adapters are torch-free Python (same shape as the Riva adapter) |
| Console Infrastructure page | Nodes are `aws` or `local` | Mac node appears with `deploy_env: mac`, hostname, and its two engines |

---

## Motivation

Dalston already supports workers that live outside the control plane host: the split-mode GPU worker is a Tailscale node that runs engine containers pointed at `redis://dalston-control-plane:6379` and the artifact bucket. Nothing in the orchestrator cares where an engine runs — it selects by registry capabilities and engines pull from per-engine Redis streams.

The blocker for a Mac is the inference stack, not the plumbing:

- The CUDA engines (`nemo`, `vllm-asr`) cannot run at all.
- The pyannote Docker image is pinned to `linux/amd64` and would run under emulation.
- Native Python with MPS works (the SDK auto-detects CUDA → MPS → CPU and `hf-asr-align-pyannote` exists for exactly this), but it is slow for diarization, memory-hungry, and carries a full torch install.

dalston-mobile has already solved on-device inference for the same two models with [FluidAudio](https://github.com/FluidInference/FluidAudio): Core ML Parakeet TDT 0.6B v3 / Parakeet Ultra and pyannote community-1 + VBx on the Neural Engine. That stack is Swift-only. This milestone brings it to Dalston using the sidecar pattern the Riva engine already uses: a standalone inference server owns the models, and thin Python adapters do everything Dalston expects of a worker (Redis streams, heartbeats, S3, typed contracts, stale-task reclaim).

### Why not share code with dalston-mobile

The batch path in dalston-mobile's `FluidTranscriptionEngine.swift` is roughly 100 of 394 lines; the rest is live mode, UI status, provenance, and speaker-turn assembly that Dalston's merge stage does itself. The reusable knowledge is small (model keys, the `int8V2` encoder workaround, token-timings-to-words, speaker label numbering) and is carried over by hand. Sharing would require splitting a sandboxed XcodeGen app into a library, making the public Dalston repo depend on a private one, and coordinating every FluidAudio bump across two repos. The Swift server lives in this repo and depends only on FluidAudio.

---

## Architecture

```
┌──────────────────────────── Control plane (EC2 or local) ───────────────────────────┐
│  Gateway ──▶ Orchestrator ──▶ Redis streams  dalston:tasks:fluidaudio               │
│                                              dalston:tasks:fluidaudio-pyannote      │
│                               S3 / MinIO  (request.json, audio, response.json)      │
└──────────────────────────────────────┬──────────────────────────────────────────────┘
                                       │ Tailscale
┌──────────────────────────────────────┴──────────── Mac (Apple Silicon) ─────────────┐
│                                                                                      │
│   Python adapter            Python adapter                                           │
│   engine_id=fluidaudio      engine_id=fluidaudio-pyannote                            │
│   (engine-sdk, httpx,       (engine-sdk, httpx,                                      │
│    boto3; no torch)          boto3; no torch)                                        │
│   XREADGROUP / heartbeat    XREADGROUP / heartbeat                                   │
│   S3 get audio ─┐           S3 get audio ─┐                                          │
│                 │  multipart POST          │  multipart POST                         │
│                 ▼                          ▼                                         │
│   ┌──────────────────────────────────────────────────────────┐                       │
│   │ fluidaudio-server  (Swift, 127.0.0.1:9200)               │                       │
│   │   GET  /health   GET /v1/capabilities                    │                       │
│   │   POST /v1/transcribe   → AsrManager (Parakeet, Core ML) │   Neural Engine       │
│   │   POST /v1/diarize      → OfflineDiarizerManager         │                       │
│   │   models: ~/Library/Application Support/FluidAudio/Models│                       │
│   └──────────────────────────────────────────────────────────┘                       │
│                 │  JSON (text, words, duration)  │  JSON (segments)                  │
│                 ▼                                ▼                                   │
│   build_transcript() → S3 put            DiarizationResponse → S3 put                │
│   XADD task.completed                    XADD task.completed                         │
└──────────────────────────────────────────────────────────────────────────────────────┘
```

Design points:

- **One Swift process, two stage endpoints.** Both adapters call the same server. The server is single-tenant on the Mac and binds loopback only.
- **Adapters own the Dalston contract.** The server returns a minimal JSON shape of its own (below). The adapters convert into `Transcript` / `DiarizationResponse` through the SDK's `build_transcript`, `build_segment`, `build_word` helpers, exactly as the Riva adapter does. The server never imports or mirrors Dalston's Pydantic schema.
- **Multipart upload, not shared paths.** The adapter uploads the materialized audio to the server. Over loopback this is cheap (16 kHz PCM WAV is ~115 MB/hour) and keeps the server ignorant of the adapter's temp directory. A zero-copy `audio_path` form field is a follow-up, not v1.
- **Native only, loopback only.** Both adapters run in a Python venv on the Mac and the server binds `127.0.0.1`. A Docker-hosted adapter would need the server reachable on `host.docker.internal`, which contradicts loopback-only; it is not offered in v1.
- **Server-side bounded queue, not 503.** The plain batch runner processes one task at a time (`max_workers=1` in `runner.py`), so contention is only transcribe-vs-diarize. The server holds a queue of depth 2 (one per stage) and serializes inference on the Neural Engine; a request beyond that returns `503`. Adapters never retry-loop: a `503` is a task failure, which the stream redelivers.
- **No base-class chunking.** FluidAudio's URL entry points stream from disk (dalston-mobile measured a 38-minute file comfortably); the adapters do not override `get_max_audio_duration_s()` (so no Silero VAD is needed, unlike the Riva image) and the diarize adapter does not use `run_chunked_diarization`. `max_audio_duration` in the engine.yaml files is set from measurement during 104.5, not assumed.
- **Model keys are Hugging Face repo IDs.** For the transcribe stage the orchestrator hands engines `model.source or model.id`, not the YAML `loaded_model_id` (`_resolve_loaded_model_id` in `engine_selector.py`; only `onnx` is special-cased). The server therefore keys its ASR models by the FluidAudio Hugging Face repo ID, and each model YAML sets `source` and `loaded_model_id` to that same string. Diarize uses `loaded_model_id` and is set the same way for consistency.
- **Models are FluidAudio's.** The server lets FluidAudio download and compile Core ML models from Hugging Face into its default models directory on first use. Dalston's model registry still needs `ready` rows for the orchestrator to route; see 104.2 for how readiness is satisfied without the adapter touching model files.
- **In the catalog, not `local_only`.** `/v1/engines` and the console's engine list iterate `catalog.get_all_engines()`, and task timeouts come from the catalog entry's RTF (`scheduler.py`), so an engine skipped by `generate_catalog.py` is invisible and gets the default timeout. Instead of `local_only`, these engines declare `deployment: external` (104.2), a new flag that keeps them in the generated catalog, exempts them from the catalog↔compose contract check, and marks them not `on_demand`.

---

## Steps

### 104.1: Swift inference server

**Files added:**

- `engines/stt-transcribe/fluidaudio/server/Package.swift` *(new)* — SwiftPM executable `fluidaudio-server`, macOS 15+, arm64, depends on FluidAudio pinned to the exact version dalston-mobile uses (`0.17.7` at time of writing) and a small HTTP server library (Hummingbird 2 is the suggested choice; this is an implementation detail).
- `engines/stt-transcribe/fluidaudio/server/Sources/FluidAudioServer/` *(new)*
  - `main.swift` — argument parsing (`--port`, default 9200; `--host`, default `127.0.0.1`), model preload, graceful shutdown.
  - `Models.swift` — model keys (the FluidAudio Hugging Face repo IDs, e.g. `FluidInference/parakeet-tdt-0.6b-v3-coreml`; confirm the exact IDs from FluidAudio's `Repo` enum) mapped to `AsrModelVersion` / `Repo`; the `.int8V2` encoder precision for v3 (FluidAudio issue #760). Carried over from `FluidTranscriptionEngine.swift` in dalston-mobile; the comment names that file.
  - `TranscribeHandler.swift` — `AsrManager` lifecycle (one loaded ASR model at a time, swap on demand), `transcribe(url, decoderState:, language:)`, token timings → words via FluidAudio's `buildWordTimings`.
  - `DiarizeHandler.swift` — cached `OfflineDiarizerModels`, one `OfflineDiarizerManager` per request so `clustering.numSpeakers` can be set per call; speaker labels renumbered by first appearance (`S1`, `S2`… → `SPEAKER_00`, `SPEAKER_01`…).
  - `Admission.swift` — an actor with a bounded queue (depth `MAX_QUEUED = 2`, one slot per stage) that serializes inference; the Neural Engine does not benefit from concurrency. A request arriving with the queue full gets `503`. Each adapter's runner is single-task, so under normal operation the queue never exceeds one transcribe plus one diarize.

**HTTP contract (server-side, Dalston-independent):**

```
GET  /health
  200 {"status":"ok","asr_loaded":"<repo id>"|null,"diarizer_loaded":true|false,
       "fluidaudio_version":"0.17.7","device":"ane","queue_depth":0}

GET  /v1/capabilities
  200 {"server":"fluidaudio-server","version":"<semver>",
       "asr_models":["<parakeet v3 repo id>","<parakeet ultra repo id>"],
       "diarizer_models":["<speaker diarization repo id>"],
       "languages":["en","de","fr",...]}        # the 25 Parakeet v3 languages

POST /v1/transcribe   multipart/form-data
  file        (required) audio file; WAV/PCM 16 kHz mono preferred, anything AVFoundation reads is accepted
  model       (optional) one of asr_models; default = the parakeet v3 repo id
  language    (optional) ISO 639-1; passed to FluidAudio's Language (token filter, not forcing)
  200 {"model":"<repo id>","duration":1234.5,"language":"en"|null,
       "text":"...",
       "words":[{"text":"hello","start":0.42,"end":0.71,"confidence":null}, ...],
       "timings":{"load_model_s":0.0,"transcribe_s":12.3}}
  400 unknown model / unreadable audio     503 queue full     500 inference error (message in body)

POST /v1/diarize      multipart/form-data
  file          (required)
  num_speakers  (optional, int)   → clustering.numSpeakers
  min_speakers  (optional, int)   → accepted, ignored unless FluidAudio exposes it; echoed in "warnings"
  max_speakers  (optional, int)   → same
  200 {"model":"<repo id>","duration":1234.5,
       "segments":[{"speaker":"SPEAKER_00","start":0.0,"end":4.2}, ...],
       "warnings":[],
       "timings":{"load_model_s":0.0,"diarize_s":40.1}}
```

**Deliverables:**

- `swift build -c release` produces `.build/release/fluidaudio-server`.
- `curl -F file=@sample.wav http://127.0.0.1:9200/v1/transcribe` returns words with monotonic timestamps.
- Server unit tests (Swift Testing) for word building and speaker renumbering using FluidAudio types; an integration test gated on model availability (`FLUIDAUDIO_TEST_MODELS=1`).

---

### 104.2: `deployment: external` catalog flag, `fluidaudio` transcribe adapter, model catalog entries

**Files modified:**

- `dalston/schemas/engine.schema.json` — add optional `deployment: external` (enum: `compose` default, `external`).
- `scripts/generate_catalog.py` — an `external` engine is written to the catalog like any other but skipped by the catalog↔compose contract check (today only `local_only` engines are skipped, and they are dropped from the catalog entirely).
- `dalston/orchestrator/catalog.py` — `CatalogEntry.on_demand` is `False` for external engines regardless of autoscale policy, so a pinned model on a sleeping Mac fails fast with `runtime_unavailable` instead of waiting for an autoscaler that will never launch it.

**Files added:**

- `engines/stt-transcribe/fluidaudio/engine.yaml` *(new)*
- `engines/stt-transcribe/fluidaudio/engine.py` *(new)*
- `engines/stt-transcribe/fluidaudio/fluidaudio_client.py` *(new)* — shared by both adapters (the diarize adapter imports it by relative path the same way `riva_client.py` is shared).
- `engines/stt-transcribe/fluidaudio/requirements.txt` *(new)* — `httpx` beyond `.[engine-sdk]`.
- `models/fluidaudio-parakeet-tdt-0.6b-v3.yaml` *(new)*, `models/fluidaudio-parakeet-ultra.yaml` *(new)*

**engine.yaml (key fields):**

```yaml
schema_version: "1.1"
id: stt-transcribe-fluidaudio
engine_id: fluidaudio
stage: transcribe
mode: batch
# Runs only on a Mac next to fluidaudio-server; no docker-compose service.
# Stays in the generated catalog (timeouts, /v1/engines, console) but is
# exempt from the catalog<->compose contract check and never on_demand.
deployment: external
name: FluidAudio Parakeet (Core ML, Neural Engine)
version: 1.0.0
description: |
  Thin adapter for fluidaudio-server, a Swift sidecar that runs NVIDIA
  Parakeet TDT 0.6B v3 / Parakeet Ultra as Core ML models on the Apple
  Neural Engine via FluidAudio. The adapter does no inference.

container:
  gpu: none
  memory: 1G

capabilities:
  native_streaming: false
  word_timestamps: true
  includes_diarization: false
  max_concurrency: 1
  max_audio_duration: 7200     # confirm in 104.5

input:
  audio_formats: [wav, pcm_s16le]
  sample_rate: 16000
  channels: 1

interface:
  protocol: dalston-native
  health: /health
  capabilities: /v1/capabilities
  submit: /v1/transcribe
  port: 9100

hardware:
  supports_cpu: true          # the adapter is CPU; the ANE is the server's concern
  min_ram_gb: 8

performance:
  # Deliberately pessimistic placeholders. The selector ranks word-timestamp
  # engines by rtf_gpu (lower wins), so an optimistic guess would make the
  # Mac out-rank every GPU worker for auto-routed jobs. Replaced in 104.5.
  rtf_gpu: 0.3
  rtf_cpu: 0.3
```

**engine.py:**

```python
class FluidAudioBatchEngine(BaseBatchTranscribeEngine):
    ENGINE_ID = "fluidaudio"

    def __init__(self, client: FluidAudioClient | None = None) -> None:
        super().__init__()
        self._client = client or FluidAudioClient.from_env()   # DALSTON_FLUIDAUDIO_URL, default http://127.0.0.1:9200

    def transcribe_audio(self, task_request: TaskRequest, ctx: BatchTaskContext) -> Transcript:
        params = task_request.get_transcribe_params()
        # For transcribe the orchestrator passes model.source (the HF repo id), which is
        # exactly the key fluidaudio-server uses. DALSTON_DEFAULT_MODEL is the same kind of id.
        model = params.loaded_model_id or os.environ.get("DALSTON_DEFAULT_MODEL", PARAKEET_V3_REPO)
        result = self._client.transcribe(task_request.audio_path, model=model, language=params.language)
        words = [self.build_word(text=w["text"], start=w["start"], end=w["end"]) for w in result["words"]]
        segments = words_to_segments(words, build_segment=self.build_segment)   # see note below
        return self.build_transcript(text=result["text"], segments=segments,
                                     language=result.get("language") or params.language,
                                     engine_id=self.engine_id, duration=result["duration"])

    def health_check(self) -> dict[str, Any]: ...        # proxies GET /health like RivaBatchEngine
    def get_capabilities(self) -> EngineCapabilities: ... # model_variants from GET /v1/capabilities
```

FluidAudio returns words only, and downstream stages (align, PII, merge) expect segments. The ONNX Parakeet engine already solves this in `OnnxInference._words_to_segments` (`dalston/engine_sdk/inference/onnx_inference.py`): it closes a segment on sentence-ending punctuation, which Parakeet v3 emits. This milestone promotes that logic into a stage-neutral SDK helper (`dalston/engine_sdk/segments.py`, `words_to_segments`) and has both the ONNX engine and this adapter call it, rather than adding a second copy.

**Model catalog entries and readiness:**

```yaml
# models/fluidaudio-parakeet-tdt-0.6b-v3.yaml
schema_version: "1.1"
id: fluidaudio/parakeet-tdt-0.6b-v3
engine_id: fluidaudio
# source and loaded_model_id are the SAME string: the FluidAudio HF repo id.
# Transcribe tasks receive model.source (engine_selector._resolve_loaded_model_id),
# and that is the key fluidaudio-server understands.
source: <FluidInference HF repo for parakeet v3>  # confirm from FluidAudio's Repo enum
loaded_model_id: <same repo id>
name: NVIDIA Parakeet TDT 0.6B v3 (Core ML, FluidAudio)
size_gb: 0.6
stage: transcribe
languages: [bg, hr, cs, da, nl, en, et, fi, fr, de, el, hu, it, lv, lt, mt, pl, pt, ro, sk, sl, es, sv, ru, uk]
capabilities: { word_timestamps: true, punctuation: true, capitalization: true, native_streaming: false, max_audio_duration: 7200 }
hardware: { supports_cpu: true, min_ram_gb: 8 }
performance: { rtf_gpu: 0.3, rtf_cpu: 0.3 }       # pessimistic placeholder — measured in 104.5
```

The orchestrator only routes to a model whose registry row is `ready`. Today two things set `ready`: the gateway's `pull_model`, which downloads the `source` repo from Hugging Face into S3, and `sync_from_s3`, which flips a row to `ready` when it finds the model's `.complete` marker in the bucket. The YAML loader seeds every row as `not_downloaded` and has no field for "managed by the engine". For v1 the operator clicks Pull on the Models page (or `POST /v1/models/{id}/pull`); the gateway mirrors the Core ML repo into S3 and the Mac server independently lets FluidAudio fetch the same repo into its own directory. The S3 copy is unused by the Mac. This is a known wart; Open Question 2 asks whether a small `managed_by: engine` flag belongs in this milestone instead.

---

### 104.3: `fluidaudio-pyannote` diarize adapter

**Files added:**

- `engines/stt-diarize/fluidaudio/engine.yaml` *(new)* — `engine_id: fluidaudio-pyannote`, `stage: diarize`, `deployment: external`, `gpu: none`, `max_concurrency: 1`, with a `description`.
- `engines/stt-diarize/fluidaudio/engine.py` *(new)*
- `engines/stt-diarize/fluidaudio/requirements.txt` *(new)* — `httpx`, `numpy` (see note on the overlap helper).
- `models/fluidaudio-speaker-diarization.yaml` *(new)* — `stage: diarize`, `source` and `loaded_model_id` both the FluidAudio speaker-diarization repo id, `languages: null`.

**engine.py:**

```python
class FluidAudioDiarizeEngine(Engine):
    ENGINE_ID = "fluidaudio-pyannote"

    def process(self, task_request: TaskRequest, ctx: BatchTaskContext) -> TaskResponse:
        params = task_request.get_diarize_params()
        result = self._client.diarize(task_request.audio_path,
                                      num_speakers=params.num_speakers,
                                      min_speakers=params.min_speakers,
                                      max_speakers=params.max_speakers)
        turns = [SpeakerTurn(speaker=s["speaker"], start=s["start"], end=s["end"]) for s in result["segments"]]
        speakers = sorted({t.speaker for t in turns})
        overlap_duration, overlap_ratio = overlap_stats_from_turns(turns)   # existing helper, dalston/engine_sdk/diarize_chunking.py
        return TaskResponse(data=DiarizationResponse(
            turns=turns, speakers=speakers, num_speakers=len(speakers),
            overlap_duration=overlap_duration, overlap_ratio=overlap_ratio,
            engine_id=self.engine_id, warnings=result.get("warnings", [])))

    def create_http_server(self, port: int = 9100):
        return DiarizeHTTPServer(engine=self, port=port)
```

`params.exclusive` is accepted and ignored with a warning: FluidAudio's offline pipeline already emits non-overlapping segments. `params.max_chunk_s` is ignored (no chunking).

`overlap_stats_from_turns` already exists in `dalston/engine_sdk/diarize_chunking.py` and is what pyannote-4.0 uses, so it is imported rather than re-implemented. That module imports `numpy` at top level and `numpy` is not in the `engine-sdk` extra, so the adapter's requirements add it. Moving the pure helpers out of a torch-adjacent module is a tidy-up for later, not this milestone.

---

### 104.4: Mac worker install

**Files added:**

- `engines/stt-transcribe/fluidaudio/mac/install.sh` *(new)* — creates `~/.dalston/fluidaudio/venv` (Python ≥ 3.11), `pip install -e ".[engine-sdk]" httpx` from the repo checkout, builds the Swift server in release mode, writes `~/.dalston/fluidaudio/worker.env` from a template, and loads three launchd agents.
- `engines/stt-transcribe/fluidaudio/mac/com.dalston.fluidaudio-server.plist` *(new)* — runs `fluidaudio-server`, `KeepAlive`, logs to `~/Library/Logs/dalston/`.
- `engines/stt-transcribe/fluidaudio/mac/com.dalston.fluidaudio-transcribe.plist` *(new)*, `com.dalston.fluidaudio-diarize.plist` *(new)* — each runs a wrapper script that sources the shared `worker.env`, then exports the per-adapter values (`DALSTON_METRICS_PORT` 9100 vs 9101, `DALSTON_WORKER_ID`), and execs the engine. launchd has no env-file directive, hence the wrapper. Each plist sets `WorkingDirectory` to its engine folder because `load_engine_yaml` looks only at `/etc/dalston/engine.yaml` and `./engine.yaml`. Per-adapter metrics ports matter: the runner silently skips binding when the port is taken (`is_port_in_use`), so a shared port would cost the second adapter its `/health` and `/metrics` without any error.
- `engines/stt-transcribe/fluidaudio/mac/worker.env.example` *(new)*; plain `KEY=value` only, no shell substitution:

```bash
# Control plane (Tailscale MagicDNS name of the control plane host)
REDIS_URL=redis://dalston-control-plane:6379
DALSTON_S3_BUCKET=dalston-artifacts-...
DALSTON_S3_REGION=eu-west-2
AWS_REGION=eu-west-2
# For a local `make dev` control plane instead: DALSTON_S3_ENDPOINT_URL=http://<cp-tailscale-ip>:9000
# AWS credentials: an IAM user scoped to this bucket only, stored as a named profile.
# Never the admin key. (Follow-up: read it from the macOS keychain instead of ~/.aws.)
AWS_PROFILE=dalston-worker

# Identity (shows on the Infrastructure page). generate_instance_id() keeps the first
# 12 characters of DALSTON_WORKER_ID, so keep it short; the wrapper appends the stage.
DALSTON_WORKER_ID=studio          # → instance ids "fluidaudio-studio-tx" / "fluidaudio-pyannote-studio-dz"
DALSTON_HOST_HOSTNAME=studio
DALSTON_DEPLOY_ENV=mac

# Engine behaviour
DALSTON_FLUIDAUDIO_URL=http://127.0.0.1:9200
DALSTON_DEFAULT_MODEL=<parakeet v3 repo id>
DALSTON_LOG_FORMAT=json
# Observability (only if the control plane runs the observability profile)
# OTEL_ENABLED=true
# OTEL_EXPORTER_OTLP_ENDPOINT=http://dalston-control-plane:4317
# DALSTON_METRICS_ENABLED=true
```

Not set, on purpose: `DALSTON_BATCH_MAX_INFLIGHT` / `DALSTON_TOTAL_CAPACITY` (read only by `AdmissionConfig` in unified runners; the plain runner is single-task), `DALSTON_MODEL_SOURCE` (the adapter loads no models), `HF_TOKEN` (FluidAudio's Core ML repos are not gated; confirm in 104.5).

- `docs/guides/14-mac-neural-engine-worker.md` *(new)* — prerequisites (Apple Silicon, macOS 15+, Xcode command line tools, Tailscale joined to the same tailnet as the control plane, `tailscale ping dalston-control-plane`), install, verify, stop/start (`launchctl bootout` / `bootstrap`), model pull, the sleep story (below), and security notes (loopback-only server, Redis is unauthenticated and protected only by the tailnet and security groups, bucket-scoped IAM key, Tailscale ACL tag for the worker).
- `docs/guides/02-pick-your-deployment.md` — add path "6. Split + Mac Neural Engine worker" and update the CPU vs GPU table.
- `docs/plan/README.md` — index M104.

**Sleep and disconnect, stated honestly:** the heartbeat TTL is 60 s, and `claim_stale_from_dead_engines` hands a stale task to *another live instance of the same engine_id*. With one Mac there is no other instance, so a task already on `dalston:tasks:fluidaudio` when the Mac sleeps waits until the Mac wakes or the task hits its timeout (3 × catalog RTF × duration). Pinned jobs submitted while the Mac is asleep fail immediately with `runtime_unavailable` (external engines are never `on_demand`); auto-routed jobs go to whichever other engine is healthy. `caffeinate -i` prevents idle sleep but not lid-close sleep; the guide tells the operator to run the Mac with the lid open or set `pmset` accordingly, and to stop the agents before travelling.

**Also modified:**

- `web/src/pages/Infrastructure.tsx` — the badge (line ~64) and sort (line ~283) treat everything that is not `aws` as "Local dev". Add `mac`. No backend change: `node_identity.py` already passes any non-`aws` `DALSTON_DEPLOY_ENV` value through and the registry stores it.

---

### 104.5: Measurement and catalog numbers

**Files modified:**

- `engines/stt-transcribe/fluidaudio/engine.yaml`, `engines/stt-diarize/fluidaudio/engine.yaml`, the three `models/fluidaudio-*.yaml` — replace placeholder `rtf_*` and `max_audio_duration` with measured values.

**Deliverables:**

- Run the `docs/guides/50-performance-and-rtf.md` method on an M-series Mac for 5, 30 and 120 minute files for both stages; record RTF, peak RSS of `fluidaudio-server`, and whether the 120-minute diarization completes without chunking. Put the table in the guide.
- The selector's auto path (`_rank_capable_engines`) scores word-timestamp support first and then `rtf_gpu` (lower wins) from the *catalog*, so these numbers decide whether the Mac captures auto-routed transcribe jobs ahead of the AWS `nemo` worker when both are online. The same catalog RTF sets task timeouts in the scheduler. Record both implications in the guide; routing preference itself is a Non-Goal.
- Confirm the exact FluidAudio Hugging Face repo IDs and that none is gated (no `HF_TOKEN` needed).

---

## Non-Goals

- **Realtime adapter** — FluidAudio's `SlidingWindowAsrManager` and streaming Sortformer could back a realtime engine the way `riva/rt_engine.py` does, but it needs the realtime SDK's WebSocket server and an advertised `DALSTON_WORKER_ENDPOINT` reachable over the tailnet. Separate milestone once batch is proven.
- **Sharing Swift code with dalston-mobile** — see Motivation. Knowledge is copied with attribution; packages are not shared.
- **MLX or MPS paths** — the SDK already supports MPS for Python engines; this milestone is specifically the Core ML / Neural Engine stack.
- **Location-aware or priority routing** — "prefer the Mac when online" or "prefer AWS GPU" needs a per-engine weight in `engine_selector.py`. Out of scope; today the static catalog `rtf_gpu` decides, which is why 104.5's measured numbers matter.
- **Docker-hosted adapters** — would require the Swift server to listen beyond loopback for `host.docker.internal`. Native venv only in v1.
- **Credentials in the macOS keychain** — v1 uses a bucket-scoped IAM profile in `~/.aws`; keychain-backed credentials are a follow-up.
- **Presigned URL transport (M77)** — the adapters use boto3 with an IAM key like every other engine. M77 removes the key; it is not a prerequisite.
- **S3 → FluidAudio model sync** — letting the Mac fetch Core ML models from the artifact bucket instead of Hugging Face. Follow-up once the registry's `ready` semantics for externally-managed models are settled.
- **Autoscaler integration** — the M91 autoscaler launches EC2; a Mac is launched by `launchctl`. The worker is either on or off.
- **Running the server on iOS** — the server is a macOS command-line tool; iOS stays dalston-mobile's domain.

---

## Deployment

No control-plane changes are required to start using a Mac worker (the catalog and console change in 104.4 are additive). Order on the Mac: install Tailscale and join the tailnet → `install.sh` → pull the three models on the Models page → confirm the two engines on the Infrastructure page → submit a job.

---

## Verification

```bash
# On the Mac: server alone
curl -s http://127.0.0.1:9200/health | jq .
curl -s -F file=@tests/fixtures/test_audio.wav http://127.0.0.1:9200/v1/transcribe | jq '.words | length'
curl -s -F file=@tests/audio/test_stereo_speakers.wav -F num_speakers=2 http://127.0.0.1:9200/v1/diarize | jq '.segments | length'

# On the Mac: adapters without Redis/S3 (SDK local runner, M52). The server must be up:
# the adapter has no inference of its own. --engine takes the <module:Class> form
# the new-engine tutorial documents.
echo '{"language":"en","word_timestamps":true}' > /tmp/fluid-config.json
python -m dalston.engine_sdk.local_runner run \
  --engine engines/stt-transcribe/fluidaudio/engine.py:FluidAudioBatchEngine \
  --stage transcribe --audio tests/fixtures/test_audio.wav \
  --config /tmp/fluid-config.json --output /tmp/fluid-transcribe.json
jq '.data.segments[0].words[0]' /tmp/fluid-transcribe.json     # PASS: has text/start/end

# From anywhere on the tailnet: both engines registered and heartbeating
curl -s -H "Authorization: Bearer $DALSTON_API_KEY" http://dalston-control-plane:8000/v1/engines \
  | jq '.engines[] | select(.id|startswith("fluidaudio")) | {id, status, loaded_model}'
# PASS: two rows with status "running". ("available" means catalogued but no heartbeat;
# "unhealthy" means heartbeating but at capacity.) The Mac badge and hostname are
# checked on the console's Infrastructure page, which is heartbeat-driven (M78).

# End to end, pinned to the Mac models
curl -s -X POST http://dalston-control-plane:8000/v1/audio/transcriptions \
  -H "Authorization: Bearer $DALSTON_API_KEY" \
  -F file=@meeting.wav -F model=fluidaudio/parakeet-tdt-0.6b-v3 \
  -F speaker_detection=diarize -F model_diarize=fluidaudio/speaker-diarization | jq '.id'
# then poll /v1/audio/transcriptions/{id}; PASS: status completed, speakers > 0,
# and the job's task list shows engine_id fluidaudio and fluidaudio-pyannote

# Engine schema still validates; regenerated catalog is unchanged (local_only engines are skipped)
python -m dalston.tools.validate_engine --all
python scripts/generate_catalog.py && git diff --exit-code dalston/orchestrator/generated_catalog.json
```

---

## Open Questions (for review)

Resolved during the first review pass (kept for the record):

- *Is a registered, heartbeating engine outside the catalog selectable?* Yes in both paths: the auto path uses the registry only, and the pinned path consults the catalog only when the engine is unhealthy. But such an engine is invisible to `/v1/engines` and the console engine list and gets the default task timeout, which is why the spec now uses `deployment: external` instead of `local_only`.
- *Two engine IDs vs one `includes_diarization` engine?* Two. A combined engine would have to assemble word-level speaker turns in the adapter for a +1 in ranking, and would lose independent routing of the two stages.
- *503 vs queueing in the server?* A bounded server-side queue. Adapters are single-task, so the only contention is transcribe-vs-diarize; a retry loop in the adapter would have to honour the task deadline for nothing.
- *Segments from words?* Promote the ONNX engine's sentence-punctuation split into the SDK; merge reads `segments[].words`, so segments are required.

Still open:

1. **Readiness without a Dalston-managed download.** Pulling a Core ML repo into S3 only to satisfy `status == ready` is a workaround. Options: (a) accept it for v1; (b) a `managed_by: engine` flag on the model row, set from the YAML, that makes the row `ready` when a heartbeating engine for that `engine_id` is present; (c) have the Mac install script write the `.complete` marker `sync_from_s3` looks for. Recommendation: (b), small and honest, but it touches the model registry service, so confirm it belongs here rather than in a M46 follow-up.
2. **`deployment: external` scope.** The flag is introduced for this engine pair. Should `hf-asr-align-pyannote` (today `local_only`) migrate to it in the same change, retiring `local_only`, or stay as is?
3. **Pessimistic placeholder RTF.** Until 104.5 measures, the catalog RTF is set so GPU workers out-rank the Mac for auto-routed jobs. Is that the right default, or should the Mac win by default on a control plane that has no GPU worker online (it will, since ranking only considers healthy engines; the question is the both-online case)?

---

## Checkpoint

- [ ] `swift build -c release` in `engines/stt-transcribe/fluidaudio/server` produces `fluidaudio-server`; `/health`, `/v1/capabilities`, `/v1/transcribe`, `/v1/diarize` behave per contract
- [ ] `fluidaudio` adapter passes the SDK local runner with a fixture WAV and produces a `Transcript` with word timestamps
- [ ] `fluidaudio-pyannote` adapter passes the SDK local runner and produces a `DiarizationResponse` with ≥ 2 speakers on the two-speaker fixture
- [ ] `deployment: external` is in the engine schema; both `engine.yaml` files pass `validate_engine --all`; both engines appear in `generated_catalog.json` with `on_demand: false`; the catalog↔compose contract check passes
- [ ] Three `models/fluidaudio-*.yaml` entries seed on gateway startup and reach `ready` after Pull; a transcribe task carries the FluidAudio repo id as `loaded_model_id`
- [ ] `install.sh` on a clean Apple Silicon Mac results in three running launchd agents; `/v1/engines` shows both engines as `running` with distinct metrics ports; the Infrastructure page shows the node with a Mac badge
- [ ] End-to-end job pinned to the Mac models completes with speakers assigned
- [ ] Mac sleep/disconnect test: a task in flight resumes after wake and completes; a pinned job submitted while asleep fails with `runtime_unavailable`; an auto-routed job goes elsewhere; no job is lost or duplicated
- [ ] RTF table for 5/30/120-minute files is in the guide and the placeholder `rtf_*` values are replaced
- [ ] Guide `14-mac-neural-engine-worker.md` published and linked from `02-pick-your-deployment.md`
