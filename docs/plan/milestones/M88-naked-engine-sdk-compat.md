# M88: Naked Engine SDK Compat + Single-Engine AWS Deploy

|                  |                                                                                                                                                                                                                      |
| ---------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Goal**         | Let a single engine container serve OpenAI and ElevenLabs SDK traffic directly, and make it one-command deployable on AWS over Tailscale — no gateway, no orchestrator, no control plane                             |
| **Duration**     | 3–4 days                                                                                                                                                                                                             |
| **Dependencies** | M79 (Leaf Engine HTTP API)                                                                                                                                                                                           |
| **Deliverable**  | `/v1/audio/transcriptions` + `/v1/speech-to-text` endpoints on `TranscribeHTTPServer` and `CombinedHTTPServer`; `dalston-aws engine up/down/status` subcommand; pinned torch/torchcodec ABI in `docker/Dockerfile.base-pyannote` |
| **Status**       | Completed                                                                                                                                                                                                            |

## User Story

> *"As a solo user, I want to point my existing OpenAI or ElevenLabs SDK at a single GPU box on my tailnet and transcribe — no self-hosted gateway, no orchestrator, no Redis cluster. Just one engine, one job at a time, charged only while it's running."*

---

## Outcomes

| Scenario                                                   | Before                                                                                  | After M88                                                                                                |
| ---------------------------------------------------------- | --------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------- |
| OpenAI SDK → a Dalston engine                              | Required Gateway to mount `/v1/audio/transcriptions`; naked engine only spoke `/v1/transcribe` | Engine container answers `POST /v1/audio/transcriptions` (sync only) directly                            |
| ElevenLabs SDK → a Dalston engine                          | Same — Gateway only                                                                     | Engine container answers `POST /v1/speech-to-text` (sync only) directly                                  |
| Composite engine (e.g. `whisper-align-pyannote`) over SDK  | Gateway only                                                                            | Composite registers the same compat routes; transcribe path works through HTTP children                  |
| "Single engine, one job at a time" AWS deployment          | Had to run full `setup -t gpu` + control-plane + `launch gpu`                            | `dalston-aws engine up <preset>` — one EC2 box, Tailscale-joined, no control plane                       |
| Pyannote diarization on the base image                     | `import pyannote.audio` SIGSEGV against torch 2.11 — torchcodec ABI mismatch             | torch, torchaudio, torchcodec pinned together; pyannote loads cleanly                                    |

---

## Motivation

M79 made every engine HTTP-addressable behind a Dalston-native contract (`POST /v1/transcribe`). That unlocks composites and push-based dispatch, but it still required the Gateway to translate SDK-shaped traffic (OpenAI `/v1/audio/transcriptions`, ElevenLabs `/v1/speech-to-text`) into the native schema. For a solo user who only wants to run *one* engine on *one* GPU — the "kill a Whisper bill" use case — the Gateway is extra weight: a Postgres, a Redis, a stateful orchestrator, an API-key service, none of which are needed when the engine is already private on a tailnet and answers one job at a time.

M88 makes the leaf engine (and its composite wrappers) directly usable by the OpenAI and ElevenLabs SDKs, and provides a one-command AWS deployment for that topology. It also fixes a latent ABI regression in the pyannote base image discovered while validating composite diarization end-to-end.

The compat routes are deliberately **sync-only, single-channel, no persistence, no webhooks, no rate limits**. Anything that requires Gateway-side state (async jobs, webhooks, export formats beyond `text|json|verbose_json`, multi-channel, diarization, speaker-id) returns 400 — use the Gateway if you need those.

---

## Architecture

```
┌────────────────────────────────────────────────────────────┐
│                    Engine Container (GPU EC2)               │
│                                                             │
│   TranscribeHTTPServer  /  CombinedHTTPServer               │
│                                                             │
│     POST /v1/transcribe                 (native)            │
│     POST /v1/audio/transcriptions       (OpenAI)     NEW    │
│     POST /v1/speech-to-text             (ElevenLabs) NEW    │
│     GET  /health                                            │
│                                                             │
│                       │                                     │
│                       ▼                                     │
│              engine.process(TaskRequest)                    │
│                       │                                     │
│   ┌───────────────────┴──────────────────────┐              │
│   │                                          │              │
│   ▼                                          ▼              │
│ LeafEngine (direct inference)     CompositeEngine ──HTTP──▶ │
│                                                 (children) │
│                                                             │
│   Redis sidecar  (engine-SDK registry heartbeat only)       │
│                                                             │
│   Port 9100 on tailnet hostname → no public IP              │
└────────────────────────────────────────────────────────────┘
                               │
                               ▼
                        Tailscale (41641/UDP)
                               │
                               ▼
                       User laptop / tailnet
                 OpenAI / ElevenLabs SDK client
```

### Admission-level guarantees (single-engine AWS deployment)

- `DALSTON_TOTAL_CAPACITY=1`, `DALSTON_BATCH_MAX_INFLIGHT=1`, `DALSTON_RT_RESERVATION=0` → a second concurrent HTTP request is rejected by the `AdmissionController` (`TaskDeferredError` → 503). Queueing is the caller's responsibility.
- `DALSTON_MAX_LOADED_MODELS=1` → if a request specifies a different model than the resident one, the LRU evictor unloads the current model and downloads the new one from HF.
- `DALSTON_MODEL_SOURCE=hf` with `HF_HOME=/data/models` on instance-store NVMe → models re-download on each boot, no persistent EBS cost.

---

## Steps

### 88.1: `TranscribeHTTPServer` mounts OpenAI + ElevenLabs compat routes

**Files modified:**

- `dalston/engine_sdk/http_compat.py` *(new)* — OpenAI + ElevenLabs form parsing, response shaping.
- `dalston/engine_sdk/http_transcribe.py` — call `register_compat_endpoints(app, engine, engine_id)` from `_register_stage_endpoints`.
- `tests/unit/test_http_compat_server.py` *(new)* — TestClient-driven coverage of both routes against a mocked engine.

**Deliverables:**

```python
# http_compat.py (public surface)
def register_compat_endpoints(
    app: FastAPI,
    engine: Engine,
    engine_id: str,
) -> None:
    """Attach OpenAI + ElevenLabs compatible POST routes to ``app``."""
```

Shape handled by `_format_openai`:

- `response_format=json` → `{"text": "..."}`
- `response_format=text` → `text/plain` body
- `response_format=verbose_json` → `task`, `language`, `duration`, `segments[]`, `model`, and `words[]` when `timestamp_granularities` includes `"word"`

Shape handled by `_format_elevenlabs`: `language_code`, `language_probability`, `text`, `words[]` (with `type`, `speaker_id`, `logprob`).

Features rejected with 400 (Gateway required):

- `diarize=true`, `webhook=true`, `timestamp_granularities` with `response_format != verbose_json`

---

### 88.2: `CombinedHTTPServer` inherits the compat routes

**Files modified:**

- `dalston/engine_sdk/http_combined.py` — when `"transcribe" in caps.stages`, call `register_compat_endpoints(...)` in addition to per-stage endpoints.
- `tests/unit/test_http_combined_server.py` — add compat-route coverage on the composite.

**Deliverables:**

Composite engines (`whisper-align-pyannote`, any future combo with a transcribe stage) expose the same OpenAI / ElevenLabs contract. Compat calls always set `stage="transcribe"` so the composite dispatches to its transcribe child only — diarize is gated by the 400 above.

---

### 88.3: Accept OpenAI SDK's bracketed `timestamp_granularities[]`

**Files modified:**

- `dalston/engine_sdk/http_compat.py` — declare two `Form()` parameters, merge in the handler.
- `tests/unit/test_http_compat_server.py` — regression test posting `timestamp_granularities[]=word` directly.

**Deliverables:**

```python
timestamp_granularities: Annotated[list[str] | None, Form()] = None,
timestamp_granularities_bracket: Annotated[
    list[str] | None, Form(alias="timestamp_granularities[]")
] = None,
...
# OpenAI SDK sends timestamp_granularities[]=; curl sends the
# unbracketed repeated field. Accept both.
granularities = timestamp_granularities or timestamp_granularities_bracket
```

Without this merge, the OpenAI Python SDK's PHP-style array serialization (`timestamp_granularities[]=word`) is silently dropped, so word-level timestamps vanish from `verbose_json`. End-to-end verification on a 4-min WAV: OpenAI SDK returned 0 words before, 647 words after.

---

### 88.4: Disable `response_model` on the OpenAI route

**Files modified:**

- `dalston/engine_sdk/http_compat.py`

**Deliverables:**

`@app.post("/v1/audio/transcriptions", response_model=None)` — FastAPI refuses to build the app when a route returns a union that includes `starlette.responses.Response`, which is necessary to support `response_format=text`.

---

### 88.5: Pin torch + torchaudio + torchcodec together in base-pyannote

**Files modified:**

- `docker/Dockerfile.base-pyannote`

**Deliverables:**

```dockerfile
RUN pip install --no-cache-dir --break-system-packages \
        "torch>=2.11,<2.12" "torchaudio>=2.11,<2.12" \
        "torchcodec>=0.11,<0.12" \
        "pyannote.audio>=4.0.0,<5.0.0" \
        "huggingface_hub>=1.0.0"
```

Root cause: `torchcodec 0.10` resolves the `c10::MessageLogger(const char*, int, int, bool)` constructor, which `torch 2.11` removed. The dlopen raises `OSError: undefined symbol: _ZN3c1013MessageLoggerC1EPKciib`, and when `scipy` has already been loaded in the process the same dlopen crashes with SIGSEGV instead of a clean OSError. Pinning the torch minor prevents the pair from drifting, while the torchcodec minor pin still allows patch releases in.

End-to-end verification after the pin: 4-min test WAV through `whisper-align-pyannote` → 53 transcribe segments, 647 words, 105 diarize turns, 4 distinct speakers — all three stages complete.

---

### 88.6: `dalston-aws engine up/down/status` subcommand

**Files modified:**

- `infra/scripts/dalston-aws` — `EngineDeployment` dataclass, `ENGINE_STATE_FILE` separate from the full-stack state, `generate_single_engine_user_data()`, `_single_engine_docker_run_block()`, `cmd_engine_up/down/status`, subparser wiring. `cmd_teardown` also terminates the engine instance before dropping its SG so AWS doesn't refuse to delete the SG.

**Deliverables:**

```
dalston-aws engine up <preset> [--spot | --on-demand] [--gpu-type TYPE]
dalston-aws engine status
dalston-aws engine down
```

Presets are the keys of `GPU_ENGINE_PRESETS`: `faster-whisper`, `nemo`, `onnx`, `hf-asr`, `vllm-asr`, `pyannote`. Defaults: spot pricing, `g4dn.xlarge`.

Cloud-init generates a self-contained user-data that:

1. Joins Tailscale with hostname `dalston-engine-<preset>` (reuses the existing Tailscale auto-join block used by full GPU workers, so SSM parameters and systemd re-join behaviour are identical).
2. Mounts instance-store NVMe at `/data/models` (ephemeral — re-downloaded each boot).
3. Starts a `redis:7-alpine` sidecar on localhost:6379 so the engine SDK's registry heartbeat has somewhere to write (no shared control plane needed).
4. Pulls `$GHCR_REGISTRY/<image>:latest` and starts the engine container with the admission envs from the Architecture section.
5. Names the container using the preset's existing `container` field so SSH debugging matches the full-stack deployment.

State lives in `~/.dalston/engine-state.yaml` (separate file from `aws-state.yaml`) so the single-engine lifecycle doesn't interact with the full-stack `gpu_workers` list.

**Reachability**: `http://dalston-engine-<preset>:9100` from any tailnet member. No ALB, no public IP, no ACM cert.

---

## Safety Properties

- **Teardown completeness.** `cmd_teardown` loads `ENGINE_STATE_FILE` in addition to `aws-state.yaml` and terminates the single-engine instance *before* dropping the shared GPU security group. Without this ordering, AWS refuses to delete the SG because the instance still holds an ENI. Without this inclusion, a teardown that "succeeded" would leave a billable GPU EC2 alive.
- **`engine down` failure safety.** If `ec2.terminate_instances` raises (permissions, throttle, transient), `engine down` **retains** `ENGINE_STATE_FILE` so the next `down` retry finds the instance. Only a confirmed `terminated`/`shutting-down` state or a vanished instance clears local state.
- **`engine up` stopped-instance refusal.** An existing `stopped` tracked instance blocks `engine up` — the user must `engine down` (terminates) first. Otherwise the state file would be overwritten with the new instance id and the stopped one would be orphaned (still charged for EBS).

---

## Non-Goals (Gateway is still required for)

- Async jobs and job history (no Postgres on the engine box)
- Webhook delivery
- Additional export formats (SRT, VTT, docx, pdf)
- Multi-channel audio per request
- Diarization over the OpenAI or ElevenLabs routes (use `POST /v1/transcribe_and_diarize` on a composite engine, or use the Gateway)
- Speaker ID, entities, PII detection, audio redaction
- API-key auth (the engine trusts its tailnet)
- Queue fan-out across multiple engines

For any of these, run the Gateway.

---

## Validation

End-to-end manual check (rebuilt faster-whisper and composite images on the branch):

| API                                         | Audio               | Words returned | Notes                                                 |
| ------------------------------------------- | ------------------- | -------------- | ----------------------------------------------------- |
| `POST /v1/transcribe` (native)              | 4-min mono 16 kHz   | 647            | Baseline                                              |
| OpenAI SDK `verbose_json + ["word"]`        | 4-min mono 16 kHz   | 647            | Bracketed-array fix confirmed (0 → 647 before/after)  |
| ElevenLabs SDK                              | 4-min mono 16 kHz   | 647            | `language_code=en`, `probability=1.0`                  |
| Composite `/v1/transcribe_and_diarize`      | 4-min mono 16 kHz   | 647 + 105 turns | 4 distinct speakers, all three stages complete         |

Unit tests: [tests/unit/test_http_compat_server.py](../../../tests/unit/test_http_compat_server.py) — 16 cases covering `json`, `text`, `verbose_json`, word-granularity (both form shapes), 400 guards, keyterms forwarding, and ElevenLabs rejection of `diarize=true` / `webhook=true`.
