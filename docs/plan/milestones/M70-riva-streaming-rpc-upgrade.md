# M70: Riva NIM Engine Integration

| | |
|---|---|
| **Goal** | Implement batch and RT Riva engines from scratch against a NIM gRPC sidecar, then upgrade from `offline_recognize()` to `streaming_recognize()` with per-path `interim_results` configuration |
| **Duration** | 5–8 days |
| **Dependencies** | M63 (engine unification), M64 (registry unification) |
| **Primary Deliverable** | Two production-ready engines (`stt-batch-transcribe-riva`, `stt-rt-riva`) using `streaming_recognize()` — batch streams audio in chunks with no timeout risk; RT emits native NIM partial results with low first-word latency |
| **Status** | Proposed |

## Outcomes

1. Batch engine processes audio files via Redis queue, streams chunks to NIM via
   `streaming_recognize()`, returns `TranscribeOutput` with word timestamps —
   identical output shape to faster-whisper. No timeout risk on long recordings.
2. RT engine accepts WebSocket sessions, pipes audio chunks directly to NIM via
   `streaming_recognize(interim_results=True)`, forwards partial transcript events
   as words are recognised and final segment events at utterance boundaries.
3. Riva NIM runs as a compose sidecar service; engines connect via gRPC (not embedded).
4. Both engines register with the unified engine registry and appear on the Engines
   console page.

## Background

The Riva engine directories (`engines/stt-transcribe/riva/` and `engines/stt-rt/riva/`)
are empty — previous implementations were removed. This milestone builds both engines
from scratch in two phases:

**Phase 1 (Bootstrap)** implements both engines using `offline_recognize()` — the simpler
unary RPC. This validates NIM connectivity, output format, registry integration, and the
RT streaming-via-retranscription pattern before adding streaming complexity.

**Phase 2 (Streaming upgrade)** replaces `offline_recognize()` with `streaming_recognize()`
in both engines. The audio source (file vs WebSocket stream) and the `interim_results`
flag are the only differences between the two paths:

| | Batch | Realtime |
|---|---|---|
| Audio source | file read in chunks | WebSocket audio chunks |
| `interim_results` | `False` | `True` |
| Response events | final segments only | interim partials + final |
| Downstream output | unchanged (`TranscribeOutput`) | unchanged (partial/final WebSocket events) |

## Architecture: NIM Sidecar Pattern

Unlike faster-whisper and parakeet (which load models in-process), Riva engines are
thin gRPC adapters that delegate inference to a Riva NIM container:

```
┌─────────────────────────────────┐
│  Unified Riva Engine            │
│  ┌────────────┐ ┌─────────────┐│     gRPC      ┌──────────────┐
│  │ Batch      │ │ RT          ││──────────────▶│  Riva NIM    │
│  │ (Redis Q)  │ │ (WebSocket) ││               │  (GPU/CUDA)  │
│  └────────────┘ └─────────────┘│               │  :50051      │
│  shared RivaClient (gRPC ch.)  │               └──────────────┘
└─────────────────────────────────┘
```

Both adapters share a single gRPC channel to the NIM sidecar via `RivaClient`.
The NIM container manages GPU memory, model loading, and inference scheduling
internally. The Dalston engine handles only I/O adaptation (file→gRPC,
WebSocket→gRPC) and registry integration.

## Unified Runner (Updated)

The original M70 design kept batch and RT as separate containers, arguing that
independent scaling justified the split. In practice, both adapters are
stateless CPU processes whose scaling is governed by the NIM sidecar's capacity,
not their own resource usage. Keeping them separate added operational overhead
(two Dockerfiles, two compose services, two deployment units) without meaningful
benefit.

The Riva engine was consolidated into `engines/stt-unified/riva/` following the
same unified runner pattern as ONNX, faster-whisper, NeMo, HF-ASR, and vLLM-ASR.
The shared resource is a `RivaClient` (gRPC channel + ASR service) rather than
a GPU model, but the runner structure is identical:

- `riva_client.py` — shared gRPC client (the "core")
- `batch_engine.py` — batch adapter, accepts injected `RivaClient`
- `rt_engine.py` — RT adapter, accepts injected `RivaClient`
- `runner.py` — creates one `RivaClient`, wires both adapters with `AdmissionController`
- Single Docker container, single compose service (`stt-unified-riva`)

## Scope

In scope:

- `engines/stt-unified/riva/` — unified engine (riva_client.py, batch_engine.py, rt_engine.py, runner.py, Dockerfile, requirements.txt, rt_engine.yaml)
- Riva NIM sidecar service in `docker-compose.yml` (behind `riva` profile)
- `DALSTON_RIVA_URI` env var (default `localhost:50051`) for NIM gRPC endpoint
- `DALSTON_RIVA_CHUNK_MS` env var (default 100 ms) for batch chunk size
- Unified registry integration (heartbeat, capabilities, model reporting)
- Model registry entries for Riva-supported models
- Phase 1→2 upgrade from `offline_recognize()` to `streaming_recognize()`
- Integration tests: batch job end-to-end, RT session end-to-end

Out of scope:

- Cache-aware streaming — that's M71 (Parakeet RNNT, different engine_id)
- Changing the Riva NIM container or NGC model configuration
- Adding new Riva model variants

## Tactics — Phase 1: Bootstrap

### T1. Engine YAML definitions

```yaml
# engines/stt-transcribe/riva/engine.yaml
engine_id: riva
stage: transcribe
version: "1.0.0"
execution_profile: container
capabilities:
  languages: [en, es, fr, de, it, pt, zh, ja, ko, ru]  # NIM model-dependent
  max_audio_duration: 7200
  streaming: false
  word_timestamps: true
  includes_diarization: false
hardware:
  gpu_required: false  # engine itself is CPU; NIM sidecar needs GPU
  memory: 1G
```

```yaml
# engines/stt-rt/riva/engine.yaml
engine_id: riva
stage: transcribe
mode: realtime
version: "1.0.0"
execution_profile: container
capabilities:
  languages: [en, es, fr, de, it, pt, zh, ja, ko, ru]
  streaming: true
  word_timestamps: true
  includes_diarization: false
  max_concurrency: 8
hardware:
  gpu_required: false
  memory: 1G
input:
  encoding: pcm_s16le
  sample_rate: 16000
  channels: 1
```

Gate: `engine.yaml` files parse correctly via `EngineCapabilities.from_yaml()`.

### T2. Batch engine — `offline_recognize()`

```python
# engines/stt-transcribe/riva/engine.py

import grpc
import riva.client

from dalston.engine_sdk import Engine, EngineRequest, EngineResponse, BatchTaskContext
from dalston.common.pipeline_types import TranscribeOutput, TranscribeSegment, WordTimestamp


class RivaBatchEngine(Engine):
    def __init__(self):
        super().__init__()
        uri = os.environ.get("DALSTON_RIVA_URI", "localhost:50051")
        channel = grpc.insecure_channel(uri)
        self._asr = riva.client.ASRService(channel)

    def process(self, engine_input: EngineRequest, ctx: BatchTaskContext) -> EngineResponse:
        audio_bytes = engine_input.audio_path.read_bytes()
        language = engine_input.params.get("language", "en")

        config = riva.client.RecognitionConfig(
            language_code=language,
            max_alternatives=1,
            enable_word_time_offsets=True,
            enable_automatic_punctuation=True,
        )
        response = self._asr.offline_recognize(audio_bytes, config)

        segments = []
        for result in response.results:
            alt = result.alternatives[0]
            words = [
                WordTimestamp(
                    word=w.word,
                    start=w.start_time,
                    end=w.end_time,
                    confidence=w.confidence,
                )
                for w in alt.words
            ]
            segments.append(TranscribeSegment(
                text=alt.transcript,
                start=result.audio_processed - result.audio.duration if hasattr(result, 'audio') else 0.0,
                end=result.audio_processed if hasattr(result, 'audio_processed') else 0.0,
                words=words,
                confidence=alt.confidence,
            ))

        output = TranscribeOutput(
            text=" ".join(s.text for s in segments),
            segments=segments,
            language=language,
            duration=engine_input.audio_duration,
        )
        return EngineResponse(data=output)

    def health_check(self) -> dict:
        try:
            self._asr.stub.GetRivaSpeechRecognitionConfig(
                riva.client.proto.riva_asr_pb2.RivaSpeechRecognitionConfigRequest()
            )
            return {"status": "healthy", "nim": "connected"}
        except grpc.RpcError:
            return {"status": "unhealthy", "nim": "unreachable"}


if __name__ == "__main__":
    RivaBatchEngine().run()
```

Key differences from faster-whisper:

- No model loading (NIM handles it) — `_set_runtime_state` reports NIM connectivity, not loaded model
- gRPC channel created once at init, reused across tasks
- Health check probes NIM reachability, not local model state

Gate: unit tests with mocked `ASRService`; output validates as `TranscribeOutput`.

### T3. RT engine — `offline_recognize()` with streaming partials

The RT engine returns `supports_streaming() = True`, which tells `SessionHandler` to
call `transcribe()` on accumulated audio every `PARTIAL_RESULT_INTERVAL_CHUNKS` (~500ms)
during active speech. This produces interim partial results while the user is speaking.
At utterance boundaries (VAD silence detection), the full utterance audio is transcribed
once more for the final segment result with accurate word timestamps.

This re-transcribe pattern is inherent to `offline_recognize()` — the engine must
transcribe the growing audio buffer to produce intermediate text. Phase 2 replaces this
with `streaming_recognize(interim_results=True)`, where NIM itself emits incremental
tokens as audio arrives, eliminating redundant work.

```python
# engines/stt-rt/riva/engine.py

import grpc
import numpy as np
import riva.client

from dalston.realtime_sdk import RealtimeEngine, TranscribeResult


class RivaRealtimeEngine(RealtimeEngine):
    def __init__(self):
        super().__init__()
        uri = os.environ.get("DALSTON_RIVA_URI", "localhost:50051")
        channel = grpc.insecure_channel(uri)
        self._asr = riva.client.ASRService(channel)

    async def load_models(self) -> None:
        # No local models — verify NIM connectivity
        self.logger.info("riva_nim_check", uri=os.environ.get("DALSTON_RIVA_URI", "localhost:50051"))

    def transcribe(
        self,
        audio: np.ndarray,
        language: str,
        model_variant: str,
        vocabulary: list[str] | None = None,
    ) -> TranscribeResult:
        # Convert float32 [-1.0, 1.0] to int16 bytes for Riva
        audio_bytes = (audio * 32767).astype(np.int16).tobytes()

        config = riva.client.RecognitionConfig(
            language_code=language or "en",
            max_alternatives=1,
            enable_word_time_offsets=True,
            enable_automatic_punctuation=True,
        )
        response = self._asr.offline_recognize(audio_bytes, config)

        words = []
        text_parts = []
        for result in response.results:
            alt = result.alternatives[0]
            text_parts.append(alt.transcript)
            words.extend(
                {"word": w.word, "start": w.start_time, "end": w.end_time}
                for w in alt.words
            )

        return TranscribeResult(
            text=" ".join(text_parts),
            words=words,
            language=language,
            confidence=response.results[0].alternatives[0].confidence if response.results else 0.0,
        )

    def supports_streaming(self) -> bool:
        """Enable partial results during speech via SessionHandler's
        periodic re-transcription of accumulated audio."""
        return True

    def get_models(self) -> list[str]:
        return []  # NIM-managed; no local model selection

    def get_languages(self) -> list[str]:
        return ["en", "es", "fr", "de", "it", "pt", "zh", "ja", "ko", "ru"]

    def get_engine_id(self) -> str:
        return "riva"

    def get_supports_vocabulary(self) -> bool:
        return False  # Riva supports boosting but needs config mapping work


if __name__ == "__main__":
    import asyncio
    asyncio.run(RivaRealtimeEngine().run())
```

**Streaming behaviour (Phase 1):**

| Phase | What happens | Message type |
|---|---|---|
| During speech | `SessionHandler` calls `transcribe()` on growing audio buffer every ~500ms | `TranscriptPartialMessage` (interim) |
| At utterance end (VAD silence) | `SessionHandler` calls `transcribe()` on complete utterance audio | `TranscriptFinalMessage` (final segment with word timestamps) |

This matches the existing parakeet streaming pattern. The difference is that parakeet's
`transcribe()` is a local GPU call (~50ms), while Riva's is a gRPC round-trip (~100-200ms).
Partial result quality is acceptable because `offline_recognize()` produces valid transcripts
on partial audio — just without the trailing words that haven't arrived yet.

Gate: unit tests with mocked gRPC; RT session lifecycle tests pass with both partial
and final events verified.

### T4. Dockerfiles

Both engines are thin Python containers (no GPU, no large model downloads):

```dockerfile
# engines/stt-transcribe/riva/Dockerfile
FROM python:3.11-slim

WORKDIR /app
COPY . /repo
RUN pip install --no-cache-dir -e "/repo[engine-sdk]"

COPY engines/stt-transcribe/riva/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY engines/stt-transcribe/riva/engine.py .
COPY engines/stt-transcribe/riva/engine.yaml /etc/dalston/engine.yaml

CMD ["python", "engine.py"]
```

```
# engines/stt-transcribe/riva/requirements.txt
nvidia-riva-client>=2.14.0
grpcio>=1.60.0
protobuf>=4.25.0
```

RT Dockerfile follows the same pattern with `realtime-sdk` extra, `EXPOSE 9000`,
and standard RT env defaults (`DALSTON_INSTANCE`, `DALSTON_WORKER_PORT`, `DALSTON_MAX_SESSIONS`).

Gate: containers build successfully; `docker compose config --profiles riva` validates.

### T5. Riva NIM sidecar in docker-compose

```yaml
# docker-compose.yml — add under services

riva-nim:
  image: nvcr.io/nim/nvidia/riva-asr:latest
  environment:
    NIM_MANIFEST_PROFILE: default
    NIM_HTTP_API_PORT: 9080
    NIM_GRPC_API_PORT: 50051
  ports:
    - "50051:50051"
    - "9080:9080"
  volumes:
    - riva-model-cache:/opt/nim/.cache
  deploy:
    resources:
      reservations:
        devices:
          - driver: nvidia
            count: 1
            capabilities: [gpu]
  profiles: [riva]
  restart: unless-stopped

stt-batch-transcribe-riva:
  image: dalston/stt-batch-transcribe-riva:1.0.0
  build:
    context: .
    dockerfile: engines/stt-transcribe/riva/Dockerfile
  environment:
    <<: [*common-env, *observability-env]
    DALSTON_ENGINE_ID: riva
    DALSTON_WORKER_ID: riva-batch-1
    DALSTON_RIVA_URI: riva-nim:50051
  depends_on:
    <<: *batch-depends
    riva-nim:
      condition: service_started
  profiles: [riva]
  restart: unless-stopped

stt-rt-riva:
  image: dalston/stt-rt-riva:1.0.0
  build:
    context: .
    dockerfile: engines/stt-rt/riva/Dockerfile
  environment:
    <<: [*common-env, *observability-env]
    DALSTON_INSTANCE: stt-rt-riva
    DALSTON_WORKER_PORT: 9000
    DALSTON_MAX_SESSIONS: 8
    DALSTON_RIVA_URI: riva-nim:50051
  depends_on:
    <<: *realtime-depends
    riva-nim:
      condition: service_started
  healthcheck: *ws-healthcheck
  profiles: [riva]
  restart: unless-stopped
```

All three services gated behind `profiles: [riva]`. Activated with `make dev PROFILES=riva`
or `docker compose --profile riva up`.

Gate: `docker compose --profile riva config` validates; services start and engines
register in Redis.

### T6. Makefile integration

```makefile
dev-riva: ## Start full stack with Riva engines
 docker compose --profile riva up -d
```

Gate: `make help` shows the new target.

### Phase 1 gate

Both engines build, register, process audio via `offline_recognize()`, and appear on
the console Engines page. RT sessions produce partial events during speech and final
segment events at utterance boundaries. Unit and integration tests pass. Phase 2
proceeds only after Phase 1 is validated against a running NIM.

## Tactics — Phase 2: Streaming Upgrade

### T7. Batch engine — chunked `streaming_recognize()`

Replace the single `offline_recognize()` call with a chunked streaming request:

```python
# engines/stt-transcribe/riva/engine.py — updated process()

def _audio_chunk_iter(self, audio_bytes: bytes, chunk_ms: int, sample_rate: int):
    chunk_samples = (sample_rate * chunk_ms) // 1000
    chunk_bytes = chunk_samples * 2  # int16
    for offset in range(0, len(audio_bytes), chunk_bytes):
        yield riva.client.StreamingRecognizeRequest(
            audio_content=audio_bytes[offset : offset + chunk_bytes]
        )

def process(self, engine_input, ctx):
    audio_bytes = engine_input.audio_path.read_bytes()
    config = riva.client.StreamingRecognitionConfig(
        config=riva.client.RecognitionConfig(
            language_code=language,
            enable_word_time_offsets=True,
            enable_automatic_punctuation=True,
        ),
        interim_results=False,   # final segments only
    )
    responses = self._asr.streaming_recognize(
        config, self._audio_chunk_iter(audio_bytes, self._chunk_ms, sample_rate)
    )
    return self._build_output(responses, ctx)  # collects is_final=True only
```

`DALSTON_RIVA_CHUNK_MS` (default 100 ms) controls chunk size. Long recordings no longer
risk gRPC deadline exhaustion because audio streams incrementally.

Gate: batch contract tests — output shape identical to Phase 1 `offline_recognize()` baseline.

### T8. RT engine — native NIM interim results

Replace the re-transcription pattern with direct gRPC streaming. The engine no longer
uses `supports_streaming()` with `SessionHandler`'s periodic re-transcription — instead,
NIM itself emits partial tokens via `interim_results=True`:

```python
# engines/stt-rt/riva/engine.py — updated

def transcribe_stream(self, audio_iter, language):
    config = riva.client.StreamingRecognitionConfig(
        config=riva.client.RecognitionConfig(
            language_code=language,
            enable_word_time_offsets=True,
            enable_automatic_punctuation=True,
        ),
        interim_results=True,   # partial events forwarded to client
    )
    for response in self._asr.streaming_recognize(config, audio_iter):
        for result in response.results:
            if result.is_final:
                yield TranscribeResult(final=True, ...)
            else:
                yield TranscribeResult(final=False, text=result.alternatives[0].transcript)
```

VAD accumulation in the `SessionHandler` is replaced by direct chunk forwarding into the
gRPC iterator. The session handler no longer waits for a silence boundary before calling
the engine — audio chunks flow directly from the WebSocket to NIM.

**Streaming behaviour (Phase 2):**

| Phase | What happens | Message type |
|---|---|---|
| During speech | NIM emits partial hypotheses as audio chunks arrive | `TranscriptPartialMessage` (interim) |
| At utterance end | NIM emits `is_final=True` result with word timestamps | `TranscriptFinalMessage` (final segment) |

First-word latency drops from utterance-boundary detection time to acoustic onset time
because NIM begins decoding immediately — no waiting for VAD silence.

Gate: RT session lifecycle tests pass. Verify that `is_final=False` events reach the
client WebSocket as partial transcript events.

### T9. Timeout and large-file validation

- Record a >1-hour audio file test against the batch engine; confirm no gRPC deadline exceeded.
- Confirm `DALSTON_RIVA_CHUNK_MS` is honoured and adjustable without rebuild.

## Testing Matrix

### Phase 1 (Bootstrap)

- Unit: batch engine with mocked `ASRService` — verify `TranscribeOutput` shape matches
  faster-whisper baseline (segments, words, confidence, language).
- Unit: RT engine with mocked `ASRService` — verify `TranscribeResult` contract.
- Unit: RT engine `supports_streaming()` returns `True`.
- Unit: health check returns unhealthy when gRPC channel is down.
- Integration (requires NIM): batch job submitted via API, processed through Redis queue,
  transcript returned with word timestamps.
- Integration (requires NIM): RT WebSocket session — verify both partial events during
  speech and final segment events at utterance boundaries.
- Registry: both engines appear in `GET /api/console/engines` response with correct
  engine_id (`riva`), stage (`transcribe`), and status.

### Phase 2 (Streaming upgrade)

- Unit: mock `streaming_recognize()` responses with mix of interim and final results;
  verify batch collects final only, RT emits both.
- Integration: batch job through Redis queue — output parity against Phase 1 baseline.
- Integration: RT session — first partial event arrives before utterance end.
- Resilience: gRPC stream interrupted mid-file; verify clean error propagation.
- Latency: RT first partial event latency is measurably lower than Phase 1.
- Large-file: 90-minute audio file completes without gRPC deadline error.

## Success Criteria

- Both engines build, register, and process audio against a running Riva NIM.
- Batch `TranscribeOutput` word timestamps and text match across Phase 1 and Phase 2
  on the same audio.
- RT sessions produce partial transcript events during speech and final segment events
  at utterance boundaries. Phase 2 first-word latency is measurably lower than Phase 1.
- A 90-minute audio file completes without gRPC deadline error at default NIM settings.
- Engines appear on the web console Engines page under the Transcribe stage.
- `docker compose --profile riva config` validates cleanly.
- `make lint` and `make test` pass.

## Relationship to M71

M71 (Parakeet RNNT cache-aware streaming) addresses the same architectural gap — true
streaming partials from the model — but for a completely different engine_id. After both
milestones:

| Runtime | Streaming mechanism | Partials |
|---|---|---|
| Riva | `streaming_recognize(interim_results=True)` | Yes (M70 Phase 2) |
| parakeet-rnnt / tdt | NeMo `CacheAwareStreamingConfig` | Yes (M71) |
| parakeet-ctc | VAD segment → `model.transcribe()` | No (CTC limitation) |

faster-whisper is not autoregressive; its partial-result story (per-segment VAD)
remains unchanged and is architecturally correct for that model family.

M70 and M71 are independent and can be worked in parallel.

## References

- `engines/stt-transcribe/faster-whisper/` — batch engine reference implementation
- `engines/stt-rt/faster-whisper/` — RT engine reference implementation
- `engines/stt-rt/nemo/engine.py` — streaming partial results reference (`supports_streaming = True`)
- `dalston/realtime_sdk/session.py` — `SessionHandler` partial result logic (`PARTIAL_RESULT_INTERVAL_CHUNKS`)
- `dalston/engine_sdk/base.py` — `Engine` base class
- `dalston/realtime_sdk/base.py` — `RealtimeEngine` base class
- `dalston/common/registry.py` — unified engine registry
- Riva Python client docs: `riva.client.ASRService.streaming_recognize()`
- Riva Python client: `nvidia-riva-client` PyPI package
- NIM container: `nvcr.io/nim/nvidia/riva-asr`
