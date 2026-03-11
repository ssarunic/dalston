# M72: Inference Server Sidecar Pattern

| | |
|---|---|
| **Goal** | Extract in-process model loading into standalone inference servers so batch and RT engines become thin, CPU-only adapters sharing a single GPU-resident model — mirroring the Riva NIM sidecar pattern from M70 |
| **Duration** | 5–7 days |
| **Dependencies** | M63 (engine unification — provides TranscribeCore / ParakeetCore), M70 (Riva sidecar — architectural reference) |
| **Primary Deliverable** | `faster-whisper-server` and `parakeet-server` gRPC inference services; batch and RT engines refactored to thin gRPC clients; unified runners deprecated |
| **Status** | Proposed |

> Note (2026-03-11): Base `docker-compose.yml` removed split RT services
> `stt-rt-faster-whisper` and `stt-rt-transcribe-voxtral-mini-4b`.
> Non-Riva realtime/transcribe flows are currently served by unified runtime services.

## Context

M63 solved model duplication by running batch + RT adapters in a single process
(the "unified runner"). While this halves GPU memory usage, it couples the two
I/O paths into one failure domain — a batch OOM kills live RT sessions, and
restarting the runner drops all connections.

M70 introduced a better pattern for Riva: a standalone NIM sidecar owns the GPU
model, and both engines connect via gRPC. The engines are stateless CPU
containers with independent lifecycles. This milestone applies the same pattern
to faster-whisper and parakeet, the two runtimes that currently use the unified
runner.

### Why sidecar > unified runner

| Concern | Unified runner (M63) | Inference server sidecar |
|---|---|---|
| Fault isolation | Shared process — crash affects both | Independent processes |
| Scaling | Single process, `AdmissionController` | Scale batch replicas independently of RT |
| Deployment | Restart = drop RT sessions + drain batch | Restart batch without touching RT or model |
| AWS fit | One fat process needs GPU + Redis + WS | Model server gets GPU; adapters are tiny |
| Model lifecycle | Tied to runner process | Persistent — survives adapter restarts |
| Multi-model | LRU in one process, complex | Run multiple server instances per model |
| Consistency | Three deployment modes (batch-only, RT-only, unified) | One pattern for all runtimes (Riva, FW, parakeet) |

### Unified architecture across all runtimes

After M70, M71, and M72, every runtime follows the same sidecar topology:

```
┌──────────────┐                    ┌──────────────┐
│ Batch Engine │─── gRPC ──────────▶│              │
│ (CPU, Redis) │                    │  Model       │
└──────────────┘                    │  Server      │
                                    │  (GPU)       │
┌──────────────┐                    │  :50052      │
│ RT Engine    │─── gRPC ──────────▶│              │
│ (CPU, WS)   │                    └──────────────┘
└──────────────┘
```

| Runtime | Model server | gRPC service |
|---|---|---|
| Riva | NIM container (external) | `riva.ASR` (Nvidia-defined) |
| faster-whisper | `faster-whisper-server` (ours) | `dalston.InferenceService` |
| parakeet | `parakeet-server` (ours) | `dalston.InferenceService` |

## Outcomes

1. `faster-whisper-server` runs as a standalone GPU container exposing gRPC
   on `:50052`, wrapping `TranscribeCore` with model lifecycle management.
2. `parakeet-server` runs as a standalone GPU container exposing the same
   gRPC interface on `:50053`, wrapping `ParakeetCore`.
3. Batch and RT engines for both runtimes are refactored to thin gRPC clients —
   no `torch`, no `faster-whisper`, no `nemo` dependencies, CPU-only images.
4. Admission control moves into the inference server (it owns the GPU resource).
5. Unified runners (`engines/stt-unified/`) deprecated and removed.
6. A single AWS instance naturally hosts one inference server + both adapters
   without the coupling problems of the unified runner.

## Scope

In scope:

- Proto definition: `dalston/proto/inference.proto`
- `engines/stt-server/faster-whisper/` — server wrapping `TranscribeCore`
- `engines/stt-server/parakeet/` — server wrapping `ParakeetCore`
- Refactor batch engines (`stt-transcribe/faster-whisper/`, `stt-transcribe/parakeet/`) to gRPC clients
- Refactor RT engines (`stt-rt/faster-whisper/`, `stt-rt/parakeet/`) to gRPC clients
- Docker compose services with profile gating
- Admission control in inference server (replaces unified runner's `AdmissionController`)
- Health checks exposing loaded models and capacity
- Integration tests

Out of scope:

- Riva engines (already sidecar-native via M70)
- Model server clustering / multi-node (single instance per model is sufficient)
- Changing `TranscribeCore` or `ParakeetCore` internals
- Align, diarize, or other non-transcription stages

## Tactics

### T1. Proto definition — `dalston.InferenceService`

```protobuf
// dalston/proto/inference.proto

syntax = "proto3";
package dalston.inference;

service InferenceService {
  // Synchronous transcription — batch and RT both use this.
  // RT sends VAD-segmented utterance audio; batch sends full file audio.
  rpc Transcribe(TranscribeRequest) returns (TranscribeResponse);

  // Health and model info
  rpc GetStatus(StatusRequest) returns (StatusResponse);
}

message TranscribeRequest {
  bytes audio = 1;                // Raw audio bytes
  AudioFormat format = 2;         // How to interpret the bytes
  string model_id = 3;            // Model to use (e.g. "large-v3-turbo")
  TranscribeConfig config = 4;    // Transcription parameters
}

enum AudioFormat {
  PCM_S16LE_16K = 0;              // 16-bit signed LE, 16kHz mono (RT default)
  PCM_F32LE_16K = 1;              // 32-bit float LE, 16kHz mono
  FILE = 2;                       // Encoded file (wav, mp3, etc.) — batch
}

message TranscribeConfig {
  optional string language = 1;
  int32 beam_size = 2;
  bool vad_filter = 3;
  bool word_timestamps = 4;
  float temperature = 5;
  string task = 6;                // "transcribe" or "translate"
  optional string initial_prompt = 7;
  optional string hotwords = 8;
}

message TranscribeResponse {
  repeated Segment segments = 1;
  string language = 2;
  float language_probability = 3;
  float duration = 4;
}

message Segment {
  float start = 1;
  float end = 2;
  string text = 3;
  repeated Word words = 4;
  float confidence = 5;
  optional float avg_logprob = 6;
  optional float compression_ratio = 7;
  optional float no_speech_prob = 8;
}

message Word {
  string word = 1;
  float start = 2;
  float end = 3;
  float probability = 4;
}

message StatusRequest {}

message StatusResponse {
  string runtime = 1;             // "faster-whisper" or "parakeet"
  string device = 2;              // "cuda" or "cpu"
  repeated string loaded_models = 3;
  int32 total_capacity = 4;
  int32 available_capacity = 5;
  bool healthy = 6;
}
```

Design notes:

- **Single `Transcribe` RPC, not separate batch/RT endpoints.** The server
  doesn't care who's calling — it receives audio bytes and returns segments.
  The `AudioFormat` enum tells it how to decode. This keeps the server maximally
  simple and avoids coupling to Dalston's batch/RT distinction.
- **No streaming RPC in v1.** The current VAD-segment-then-transcribe pattern
  works over unary gRPC with acceptable latency. A `StreamingTranscribe` RPC
  can be added later for true token-streaming (analogous to Riva's
  `streaming_recognize`), but it's not needed to replace the unified runner.
- **`model_id` per request** enables runtime model switching without server
  restart — the server uses `TranscribeCore`'s existing model manager with
  TTL/LRU eviction.

Gate: `protoc` compiles cleanly; generated Python stubs import without error.

### T2. Inference server base — shared gRPC scaffold

Create a reusable server base that both faster-whisper and parakeet servers
inherit. This avoids duplicating gRPC boilerplate.

```python
# dalston/engine_sdk/inference_server.py

class InferenceServer(ABC):
    """Base class for gRPC inference servers.

    Subclasses provide a Core instance; this class handles:
    - gRPC server lifecycle
    - Admission control (semaphore-based concurrency limiting)
    - Health check endpoint
    - Graceful shutdown
    - Engine registry heartbeat
    """

    def __init__(
        self,
        core: Any,           # TranscribeCore | ParakeetCore
        port: int = 50052,
        max_concurrent: int = 4,
    ):
        self._core = core
        self._port = port
        self._semaphore = asyncio.Semaphore(max_concurrent)
        ...

    async def Transcribe(self, request, context):
        async with self._semaphore:
            audio = self._decode_audio(request.audio, request.format)
            config = self._to_core_config(request.config)
            result = await asyncio.to_thread(
                self._core.transcribe, audio, request.model_id, config
            )
            return self._to_response(result)
```

Key decisions:

- **`asyncio.to_thread` for inference.** Both `TranscribeCore.transcribe()` and
  `ParakeetCore.transcribe()` are blocking (GPU-bound). The gRPC server runs
  async, so we offload to the thread pool. The semaphore limits concurrency
  to prevent GPU OOM.
- **Semaphore replaces `AdmissionController`.** The unified runner needed
  separate batch/RT admission because it had to reserve capacity for RT.
  The inference server doesn't distinguish — it just limits total concurrent
  requests. If the server is at capacity, gRPC returns `RESOURCE_EXHAUSTED`
  and the caller (batch or RT adapter) handles backoff.
- **Registry heartbeat.** The server registers itself in the unified registry
  so the console Engines page shows the runtime and its loaded models.

Gate: unit tests with mocked core; gRPC server starts and responds to health
checks.

### T3. faster-whisper-server

```python
# engines/stt-server/faster-whisper/server.py

from dalston.engine_sdk.cores.faster_whisper_core import TranscribeCore
from dalston.engine_sdk.inference_server import InferenceServer


class FasterWhisperServer(InferenceServer):
    def __init__(self):
        core = TranscribeCore.from_env()
        port = int(os.environ.get("DALSTON_SERVER_PORT", "50052"))
        max_concurrent = int(os.environ.get("DALSTON_MAX_CONCURRENT", "4"))
        super().__init__(core=core, port=port, max_concurrent=max_concurrent)

    def get_runtime(self) -> str:
        return "faster-whisper"


if __name__ == "__main__":
    FasterWhisperServer().serve()
```

```dockerfile
# engines/stt-server/faster-whisper/Dockerfile
FROM dalston/engine-base:latest

COPY engines/stt-server/faster-whisper/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY engines/stt-server/faster-whisper/ /app/
COPY dalston/proto/ /app/proto/

ENV DALSTON_SERVER_PORT=50052
ENV DALSTON_MAX_CONCURRENT=4
ENV DALSTON_DEFAULT_MODEL_ID=large-v3-turbo
ENV DALSTON_MODEL_TTL_SECONDS=3600
ENV DALSTON_MAX_LOADED_MODELS=2

EXPOSE 50052
CMD ["python", "server.py"]
```

Requirements: `grpcio`, `grpcio-tools`, `faster-whisper`, `torch`, `numpy` — the
same GPU dependencies that currently live in the batch/RT engine images.

Gate: server starts, loads model, responds to `Transcribe` and `GetStatus` RPCs.

### T4. parakeet-server

Identical structure to T3, substituting `ParakeetCore` for `TranscribeCore`:

```python
# engines/stt-server/parakeet/server.py

from dalston.engine_sdk.cores.parakeet_core import ParakeetCore
from dalston.engine_sdk.inference_server import InferenceServer


class ParakeetServer(InferenceServer):
    def __init__(self):
        core = ParakeetCore.from_env()
        port = int(os.environ.get("DALSTON_SERVER_PORT", "50053"))
        max_concurrent = int(os.environ.get("DALSTON_MAX_CONCURRENT", "4"))
        super().__init__(core=core, port=port, max_concurrent=max_concurrent)

    def get_runtime(self) -> str:
        return "parakeet"
```

Gate: server starts with parakeet-tdt-1.1b, responds to RPCs.

### T5. gRPC client adapter — `RemoteTranscribeCore`

Create a drop-in replacement for `TranscribeCore` that delegates to the gRPC
server. Batch and RT engines switch from local core to remote core with a
single constructor change.

```python
# dalston/engine_sdk/cores/remote_core.py

class RemoteTranscribeCore:
    """gRPC client that implements the same interface as TranscribeCore.

    Drop-in replacement: batch and RT engines call .transcribe() the same
    way — the only difference is inference happens over the network instead
    of in-process.
    """

    def __init__(self, uri: str = "localhost:50052"):
        self._channel = grpc.insecure_channel(uri)
        self._stub = InferenceServiceStub(self._channel)

    def transcribe(
        self,
        audio: str | Path | np.ndarray,
        model_id: str,
        config: TranscribeConfig | None = None,
    ) -> TranscriptionResult:
        # Encode audio based on type
        if isinstance(audio, np.ndarray):
            audio_bytes = audio.astype(np.float32).tobytes()
            fmt = AudioFormat.PCM_F32LE_16K
        elif isinstance(audio, (str, Path)):
            audio_bytes = Path(audio).read_bytes()
            fmt = AudioFormat.FILE
        else:
            raise ValueError(f"Unsupported audio type: {type(audio)}")

        request = TranscribeRequest(
            audio=audio_bytes,
            format=fmt,
            model_id=model_id,
            config=self._to_proto_config(config),
        )
        response = self._stub.Transcribe(request)
        return self._from_proto_response(response)

    def shutdown(self) -> None:
        self._channel.close()
```

The key insight: because `TranscribeCore` and `RemoteTranscribeCore` share the
same `transcribe(audio, model_id, config) → TranscriptionResult` interface,
the batch and RT engine adapters require **zero logic changes** — only the
core construction changes.

Gate: `RemoteTranscribeCore` passes the same unit tests as `TranscribeCore`
(with a running server).

### T6. Refactor batch engines to gRPC clients

```python
# engines/stt-transcribe/faster-whisper/engine.py — updated

class WhisperEngine(Engine):
    def __init__(self, core=None):
        super().__init__()
        if core is None:
            uri = os.environ.get("DALSTON_INFERENCE_URI", "localhost:50052")
            self._core = RemoteTranscribeCore(uri)
        else:
            self._core = core  # backwards compat: unified runner can still inject
        ...
```

The `core=None` default means:

- **Sidecar mode (new):** No core injected → creates `RemoteTranscribeCore` → CPU-only container.
- **Unified mode (legacy):** Core injected by runner → in-process GPU inference.

This enables gradual migration. The Dockerfile drops GPU dependencies:

```dockerfile
# engines/stt-transcribe/faster-whisper/Dockerfile — updated
FROM python:3.11-slim
# No torch, no faster-whisper, no model cache
RUN pip install grpcio dalston[engine-sdk]
```

Image size drops from ~4GB to ~200MB.

Apply the same change to `engines/stt-transcribe/parakeet/engine.py`.

Gate: batch engine processes a job via gRPC to the inference server.

### T7. Refactor RT engines to gRPC clients

Same pattern as T6:

```python
# engines/stt-rt/faster-whisper/engine.py — updated

class WhisperStreamingEngine(RealtimeEngine):
    def __init__(self, core=None):
        super().__init__()
        if core is None:
            uri = os.environ.get("DALSTON_INFERENCE_URI", "localhost:50052")
            self._core = RemoteTranscribeCore(uri)
        else:
            self._core = core
        ...
```

Apply to both faster-whisper and parakeet RT engines.

Gate: RT WebSocket session produces partial + final events via gRPC server.

### T8. Docker compose services

```yaml
# Inference servers (GPU, persistent)
faster-whisper-server:
  image: dalston/faster-whisper-server:1.0.0
  build:
    context: .
    dockerfile: engines/stt-server/faster-whisper/Dockerfile
  environment:
    <<: [*common-env, *observability-env]
    DALSTON_SERVER_PORT: 50052
    DALSTON_MAX_CONCURRENT: 4
    DALSTON_DEFAULT_MODEL_ID: large-v3-turbo
    DALSTON_MODEL_PRELOAD: large-v3-turbo
  ports:
    - "50052:50052"
  volumes:
    - model-cache:/models
  deploy: *gpu-deploy
  healthcheck:
    test: ["CMD", "python", "-c",
           "import grpc; ch=grpc.insecure_channel('localhost:50052'); grpc.channel_ready_future(ch).result(timeout=5)"]
    interval: 15s
    timeout: 10s
    retries: 3
  profiles: [gpu]
  restart: unless-stopped

parakeet-server:
  image: dalston/parakeet-server:1.0.0
  build:
    context: .
    dockerfile: engines/stt-server/parakeet/Dockerfile
  environment:
    <<: [*common-env, *observability-env]
    DALSTON_SERVER_PORT: 50053
    DALSTON_MAX_CONCURRENT: 4
    DALSTON_DEFAULT_MODEL_ID: nvidia/parakeet-tdt-1.1b
    DALSTON_MODEL_PRELOAD: nvidia/parakeet-tdt-1.1b
  ports:
    - "50053:50053"
  volumes:
    - model-cache:/models
  deploy: *gpu-deploy
  profiles: [gpu]
  restart: unless-stopped

# Thin adapters (CPU, lightweight)
stt-batch-transcribe-faster-whisper:
  image: dalston/stt-batch-transcribe-faster-whisper:2.0.0
  build:
    context: .
    dockerfile: engines/stt-transcribe/faster-whisper/Dockerfile
  environment:
    <<: [*common-env, *observability-env]
    DALSTON_RUNTIME: faster-whisper
    DALSTON_WORKER_ID: fwhisper-batch-1
    DALSTON_INFERENCE_URI: faster-whisper-server:50052
  depends_on:
    <<: *batch-depends
    faster-whisper-server:
      condition: service_healthy
  # No GPU deploy, no model-cache volume
  profiles: [gpu]
  restart: unless-stopped

stt-rt-faster-whisper:
  image: dalston/stt-rt-faster-whisper:2.0.0
  build:
    context: .
    dockerfile: engines/stt-rt/faster-whisper/Dockerfile
  environment:
    <<: [*common-env, *observability-env]
    DALSTON_INSTANCE: stt-rt-faster-whisper
    DALSTON_WORKER_PORT: 9000
    DALSTON_MAX_SESSIONS: 4
    DALSTON_INFERENCE_URI: faster-whisper-server:50052
  depends_on:
    <<: *realtime-depends
    faster-whisper-server:
      condition: service_healthy
  healthcheck: *ws-healthcheck
  profiles: [gpu]
  restart: unless-stopped
```

Same pattern for parakeet batch/RT pointing at `parakeet-server:50053`.

Gate: `docker compose --profile gpu config` validates; all services start and
engines register.

### T9. Deprecate unified runners

Once sidecar mode is validated:

1. Mark unified runner env var `DALSTON_UNIFIED_ENGINE_ENABLED` as deprecated
   in settings.
2. Remove unified runner compose services (`stt-unified-faster-whisper`,
   `stt-unified-nemo`).
3. Delete `engines/stt-unified/faster-whisper/runner.py` and
   `engines/stt-unified/parakeet/runner.py`.
4. Remove `AdmissionController` and `AdmissionConfig` from engine SDK
   (admission is now a semaphore in the inference server).
5. Remove `_register_engine_modules()` import hacks.

Gate: `make lint` and `make test` pass with unified runner code removed.

### T10. CPU-only compose profile

For local development without GPU, add CPU inference server variants:

```yaml
faster-whisper-server-cpu:
  image: dalston/faster-whisper-server-cpu:1.0.0
  build:
    context: .
    dockerfile: engines/stt-server/faster-whisper/Dockerfile
    args:
      DEVICE: cpu
  environment:
    DALSTON_DEVICE: cpu
    DALSTON_DEFAULT_MODEL_ID: base
    DALSTON_MAX_CONCURRENT: 2
  profiles: [cpu]
```

Batch/RT adapters are already CPU-only — they just need
`DALSTON_INFERENCE_URI` pointed at the CPU server variant.

Gate: `make dev` starts CPU server + adapters on Mac.

## Testing Matrix

| Test | What it validates |
|---|---|
| Unit: proto round-trip | `TranscriptionResult` → proto → `TranscriptionResult` lossless |
| Unit: `RemoteTranscribeCore` with mocked stub | Same assertions as `TranscribeCore` unit tests |
| Unit: inference server with mocked core | Concurrency semaphore, error mapping, health check |
| Integration: batch job via gRPC server | File upload → batch engine → gRPC → server → transcript |
| Integration: RT session via gRPC server | WebSocket → RT engine → gRPC → server → partial + final events |
| Integration: model switching | Two requests with different `model_id` → server loads both |
| Resilience: server restart | Server restarts mid-session → RT engine reconnects, batch retries |
| Resilience: server at capacity | Semaphore full → gRPC `RESOURCE_EXHAUSTED` → batch requeues |
| Latency: gRPC overhead | Compare in-process vs gRPC transcription on same audio → overhead < 10ms |
| Image size: adapter slimming | Batch/RT images < 300MB (vs ~4GB with GPU deps) |

## Performance Considerations

### gRPC serialization overhead

| Audio length | PCM bytes (16kHz f32) | gRPC overhead | Inference time |
|---|---|---|---|
| 1s utterance (RT) | 64 KB | < 1ms | ~50ms |
| 30s utterance (RT) | 1.9 MB | ~2ms | ~500ms |
| 10 min file (batch) | 38 MB | ~10ms | ~15s |
| 1 hour file (batch) | 230 MB | ~50ms | ~90s |

For RT, the 1–2ms overhead on a 50ms+ inference call is negligible. For batch,
sending large files as single messages is fine within a compose network (local
loopback). If files exceed gRPC's default 4MB message limit, the server
config sets `max_receive_message_length` to 512MB.

### Concurrency model

The inference server uses `asyncio.to_thread` to run blocking GPU inference
in the default thread pool. The semaphore (`DALSTON_MAX_CONCURRENT`) should
match the GPU's ability to batch requests. For a single consumer GPU:

- `faster-whisper`: max 2–4 concurrent (CTranslate2 has internal batching)
- `parakeet`: max 2–4 concurrent (NeMo batch inference)

## Success Criteria

1. Both inference servers build, start, and serve transcription requests.
2. Batch and RT engines produce identical output whether using in-process core
   or remote gRPC core (output parity tests pass).
3. Adapter container images are < 300MB (no GPU dependencies).
4. A single AWS GPU instance runs one inference server + batch adapter + RT
   adapter without the coupling problems of the unified runner.
5. RT engine restart does not affect the inference server or other adapters.
6. Inference server restart triggers clean reconnection from adapters.
7. Unified runners removed from codebase and compose.
8. `make lint` and `make test` pass.

## Migration Path

1. **T1–T5:** Build proto, servers, and `RemoteTranscribeCore` — all additive.
2. **T6–T7:** Refactor adapters with backwards-compatible `core=None` default.
   Unified runners still work (inject core directly).
3. **T8:** Add sidecar compose services alongside existing unified services.
   Both modes coexist — teams can test sidecar mode while unified remains
   default.
4. **Validation window:** Run sidecar mode in staging. Verify output parity,
   latency overhead, and fault isolation.
5. **T9:** Remove unified runners after validation.
6. **T10:** CPU variants for local dev.

## Future Work (out of scope)

- **Streaming RPC:** Add `StreamingTranscribe` for true token-level streaming
  (audio chunks in → partial text out). This would replace the RT engine's
  VAD-segment-then-transcribe pattern with continuous streaming, similar to
  Riva's `streaming_recognize(interim_results=True)`. Worth doing after the
  sidecar pattern is validated.
- **Multi-GPU sharding:** Run multiple inference server replicas behind a gRPC
  load balancer for horizontal scaling.
- **TensorRT-LLM / vLLM integration:** The same sidecar pattern works for
  LLM-based engines (llm-cleanup stage).

## References

- `engines/stt-unified/faster-whisper/runner.py` — current unified runner (to be replaced)
- `engines/stt-unified/parakeet/runner.py` — current unified runner (to be replaced)
- `dalston/engine_sdk/cores/faster_whisper_core.py` — `TranscribeCore` (wrapped by server)
- `dalston/engine_sdk/cores/parakeet_core.py` — `ParakeetCore` (wrapped by server)
- `dalston/engine_sdk/admission.py` — `AdmissionController` (replaced by server semaphore)
- `docs/plan/milestones/M63-engine-unification-incremental.md` — unified runner milestone
- `docs/plan/milestones/M70-riva-streaming-rpc-upgrade.md` — Riva sidecar pattern reference
