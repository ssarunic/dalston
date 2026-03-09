# Architecture Complexity Review

**Date:** 2026-03-09
**Scope:** Tight coupling, batch/RT unification, pipeline extensibility

---

## Executive Summary

The codebase (~51k LOC Python, ~125k total) has grown two parallel subsystems
(batch and realtime) that share the same GPU, the same models, the same
inference libraries, and even the same `FasterWhisperModelManager` -- yet
require completely separate SDKs, registries, runners, container images, and
coordination infrastructure. This is the single largest source of accidental
complexity. Merging them would cut ~2,500 LOC of SDK code in half and
eliminate an entire class of operational concerns.

The second major issue is that pipeline stages are hardcoded in the
orchestrator DAG builder. Adding a new stage (VAD, emotion, speaker
verification, non-verbal events) currently requires touching 4-6 files
with stage-specific `if` branches. Complexity will grow **quadratically**
with new stages under the current design.

---

## 1. The Case for Merging Batch and Realtime Engines

### What's duplicated today

| Concern | Batch SDK | Realtime SDK | Shared? |
|---|---|---|---|
| Abstract base class | `Engine` (262 LOC) | `RealtimeEngine` (894 LOC) | No |
| Runner / server | `EngineRunner` (964 LOC) | Built into `RealtimeEngine.run()` | No |
| Registry client | `BatchEngineRegistry` (sync) | `WorkerRegistry` (async) | No |
| Model manager | `FasterWhisperModelManager` | same, wrapped in `AsyncModelManager` | Partial |
| Health check | `health_check()` | `health_check()` | Same shape, separate impl |
| Capabilities | `engine.yaml` + `EngineCapabilities` | `engine.yaml` + `EngineCapabilities` | Yes (shared type) |
| Container images | One per batch engine variant | One per RT engine variant | No |
| Session management | N/A (stateless tasks) | `SessionHandler` (1,164 LOC) | N/A |

The faster-whisper engine exists as **two separate implementations** calling
the same library, loading the same models, running the same `model.transcribe()`:

- `engines/stt-transcribe/faster-whisper/engine.py` (357 LOC) -- batch
- `engines/stt-rt/faster-whisper/engine.py` (335 LOC) -- realtime

That's ~700 lines doing the same thing with different I/O wrappers.

### The Riva model: unified engine with dual I/O

NVIDIA Riva runs a single gRPC service per model that accepts both:
- Streaming recognition (bidirectional gRPC stream)
- Batch recognition (unary RPC, file upload)

The key insight: **the model doesn't care whether audio arrives as a file or a
stream**. The difference is purely in the I/O transport layer.

### Proposed unified architecture

```
┌─────────────────────────────────────────────────┐
│              Unified Engine Container            │
│                                                  │
│  ┌──────────────┐   ┌────────────────────────┐  │
│  │ Model Manager │   │   Engine.process()     │  │
│  │ (TTL + LRU)  │   │   (pure inference)     │  │
│  └──────────────┘   └────────────────────────┘  │
│          │                    ▲                   │
│          ▼                    │                   │
│  ┌──────────────────────────────────────────┐   │
│  │           I/O Adapter Layer              │   │
│  │  ┌─────────────┐  ┌──────────────────┐   │   │
│  │  │ QueueAdapter │  │ WebSocketAdapter │   │   │
│  │  │ (batch)      │  │ (realtime)       │   │   │
│  │  └─────────────┘  └──────────────────┘   │   │
│  └──────────────────────────────────────────┘   │
│          │                    │                   │
│          ▼                    ▼                   │
│  ┌──────────────┐   ┌───────────────────┐       │
│  │ Redis Stream  │   │ WebSocket Server  │       │
│  │ (pull tasks)  │   │ (accept sessions) │       │
│  └──────────────┘   └───────────────────┘       │
│                                                  │
│  Single Registry (announces both capabilities)   │
└─────────────────────────────────────────────────┘
```

**What this eliminates:**
- Two separate SDKs (~2,500 LOC combined) collapse to one (~1,500 LOC)
- Two registry protocols collapse to one
- Duplicate engine implementations per model disappear
- Duplicate container images disappear (halves image build/push time)
- The session router can become a thin layer in the orchestrator

**What this requires giving up:**
- The ability to scale batch and RT workers independently for the same model.
  In practice this is rarely needed -- GPU memory is the bottleneck, and a
  loaded model can serve both. If you need isolation, run two instances of
  the same unified container with different `--mode` flags.

### Concrete simplification: unified engine interface

```python
class Engine(ABC):
    """Unified engine that handles both batch and streaming."""

    @abstractmethod
    def load_model(self, model_id: str) -> None: ...

    @abstractmethod
    def process_file(self, audio_path: Path, config: dict) -> StageOutput: ...

    @abstractmethod
    def process_chunk(self, audio: np.ndarray, config: dict) -> ChunkResult: ...

    def supports_streaming(self) -> bool:
        """Override to True if engine supports chunk-based processing."""
        return False
```

Batch-only engines (align, diarize, merge) only implement `process_file`.
Streaming-capable engines (transcribe) implement both. The runner checks
`supports_streaming()` and starts a WebSocket server only if True.

---

## 2. Hardcoded Pipeline Stages -- Quadratic Growth Risk

### Current state

Pipeline stages are hardcoded as string literals in `dag.py`:

```python
DEFAULT_ENGINES = {
    "prepare": "audio-prepare",
    "transcribe": "faster-whisper",
    "align": "phoneme-align",
    "diarize": "pyannote-4.0",
    "pii_detect": "pii-presidio",
    "audio_redact": "audio-redactor",
    "merge": "final-merger",
}
```

Adding a new stage (e.g., emotion recognition) requires changes in:

1. `dag.py` -- new stage constant, new Task creation block, new dependency wiring
2. `engine_selector.py` -- new selection logic, possibly new `MODEL_BACKED_STAGES` entry
3. `common/pipeline_types.py` -- new `EmotionOutput` dataclass
4. `engine_sdk/contracts.py` -- new `EmotionInputPayload`
5. `orchestrator/handlers.py` -- new stage-specific event handling (if any)
6. `orchestrator/catalog.py` -- catalog awareness (mostly automatic)
7. Docker compose -- new service definition
8. Tests for all of the above

That's **7-8 files per stage**. With N stages, the DAG builder alone has
O(N^2) conditional branches because each stage's inclusion depends on
capabilities of other stages (e.g., skip align if transcriber has native
timestamps; skip diarize if transcriber includes it).

### What NVIDIA Riva and similar systems do

Riva treats the pipeline as a **directed graph of services**, not a hardcoded
sequence. Each service declares:
- Input types it consumes (audio, transcript, diarization labels, etc.)
- Output types it produces
- Whether it's optional

The orchestrator resolves the graph from declared types, not from hardcoded
stage names.

### Proposed: declarative pipeline graph

```yaml
# engine.yaml additions
stage:
  name: emotion_recognition
  inputs:
    - kind: audio
      role: prepared
    - kind: transcript
      role: final
      required: false    # can run without transcript
  outputs:
    - kind: emotions
      role: per_segment
  optional: true         # only runs if user requests it
  after: [transcribe]    # ordering hint (not hard dependency)
```

The DAG builder becomes generic:

```python
def build_dag(job_params, available_engines):
    """Build DAG from engine declarations, not hardcoded stages."""
    requested_outputs = derive_requested_outputs(job_params)
    # e.g., {"transcript", "word_timestamps", "speaker_labels", "emotions"}

    # Topological sort of engines that produce requested outputs
    graph = resolve_engine_graph(requested_outputs, available_engines)
    return graph.to_tasks()
```

**Impact:** Adding emotion recognition becomes:
1. Write engine with `engine.yaml` declaring inputs/outputs
2. Done. No orchestrator changes.

### Complexity tradeoff

This is a real tradeoff. The current explicit DAG is easy to debug -- you can
read `dag.py` and see exactly what happens. A declarative graph is more
abstract. The recommendation: keep the declarative approach simple by
limiting it to a **linear pipeline with optional stages**, not an arbitrary
DAG. This covers all the stages you mentioned (VAD, noise reduction, speaker
recognition, emotion, non-verbal events) without needing a full graph solver.

```
PREPARE → [VAD] → [NOISE_REDUCE] → TRANSCRIBE → [ALIGN] → [DIARIZE]
    → [SPEAKER_VERIFY] → [EMOTION] → [NONVERBAL] → [PII_DETECT]
    → [AUDIO_REDACT] → MERGE
```

Each optional stage declares its position and is auto-inserted when its
engine is available AND the user requests its output.

---

## 3. Tight Coupling Hot Spots

### 3a. Engine Selector knows too much about specific stages (935 LOC)

`engine_selector.py` has stage-specific logic scattered throughout:

- Transcribe selection determines whether align and diarize are needed
- Each stage has its own selection function with bespoke ranking logic
- `MODEL_BACKED_STAGES` is an explicit set that must be maintained

**Recommendation:** Each engine's `engine.yaml` should declare what it
replaces/subsumes. E.g.:

```yaml
capabilities:
  subsumes:
    - align    # native word timestamps, no separate align needed
    - diarize  # built-in speaker detection
```

The selector becomes generic: "for each required output, find an engine;
skip stages subsumed by an already-selected engine."

### 3b. Gateway has three parallel WebSocket implementations (3,800+ LOC)

- `realtime.py` (1,412 LOC) -- Dalston native WS protocol
- `openai_realtime.py` (1,354 LOC) -- OpenAI compatible WS
- `speech_to_text.py` (1,094 LOC) -- ElevenLabs compatible REST + WS

All three proxy audio to the same RT workers. The protocol translation
should be a thin adapter, not 1,000+ LOC each.

**Recommendation:** Extract a `RealtimeProxy` core (~300 LOC) that handles
worker allocation, audio forwarding, and transcript collection. Each API
compatibility layer becomes a ~200 LOC adapter that translates protocol
messages to/from the core format.

### 3c. Docker service explosion

The docker-compose currently defines **~20 batch engine services** plus RT
services. Each engine variant (e.g., `stt-batch-transcribe-nemo`,
`stt-batch-transcribe-nemo-cpu`, `stt-batch-transcribe-nemo-onnx`,
`stt-batch-transcribe-nemo-onnx-gpu`) is a separate service with its own
container image.

With unified engines + runtime model loading, you need **one container per
inference framework** (faster-whisper, nemo, vllm), not one per model variant.
The model variant is a runtime parameter.

**Current:** ~20 service definitions
**After unification:** ~6-8 service definitions (one per framework, plus
infra services)

### 3d. Session Router vs Orchestrator -- redundant coordination

The session router (`session_router/`) is a complete parallel coordination
system:
- Its own Redis key schema (`dalston:realtime:*`)
- Its own registry protocol
- Its own health monitoring
- Its own allocation strategy

With unified engines, the orchestrator can handle both modes:
- Batch: enqueue task to Redis stream (as today)
- Realtime: find available engine instance, return its WebSocket endpoint

The session router's `acquire_worker()` / `release_worker()` becomes a
method on the orchestrator's registry. This eliminates ~1,300 LOC
(`router.py` + `allocator.py` + `health.py` + `registry.py`).

---

## 4. Will Complexity Scale Linearly with New Stages?

### Current trajectory: No, it's worse than linear

For each new stage you're considering:

| Stage | DAG changes | Selector changes | New types | New contracts | Docker |
|---|---|---|---|---|---|
| VAD (audio prep) | Conditional insertion before transcribe | If model-backed: yes | `VADOutput` | `VADInputPayload` | New service |
| Noise reduction | Conditional after VAD | If model-backed: yes | `DenoiseOutput` | `DenoiseInputPayload` | New service |
| Speaker verification | After diarize, needs transcript | Cross-stage capability check | `SpeakerVerifyOutput` | `SpeakerVerifyInputPayload` | New service |
| Emotion recognition | After transcribe, needs audio + transcript | New ranking criteria | `EmotionOutput` | `EmotionInputPayload` | New service |
| Non-verbal events | Parallel with transcribe? After VAD? | New dependency patterns | `NonVerbalOutput` | `NonVerbalInputPayload` | New service |

Each stage adds complexity to the DAG builder because:
1. It may be conditional on user parameters
2. It may be skippable based on other engine capabilities
3. Its position in the pipeline depends on what other stages are active
4. The merge stage needs to know about its outputs

With 5 new stages, the DAG builder would need to handle 2^5 = 32 possible
pipeline configurations. The `_build_dag_with_engines()` function is
already 400+ lines handling 7 stages; it would balloon to 800+ lines.

### After proposed changes: Linear growth

With declarative stage definitions in `engine.yaml`:

| Stage | Changes needed |
|---|---|
| Any new stage | 1. Write engine + engine.yaml. 2. Add output type to pipeline_types.py. |

The DAG builder, selector, and docker-compose don't change.

---

## 5. Specific Simplifications Worth the Flexibility Loss

### 5a. Drop per_channel speaker detection mode

The `per_channel` mode in `dag.py` creates parallel transcription tasks
per audio channel. This adds ~150 lines of DAG builder logic and is rarely
used (stereo call-center recordings). If you need it, model it as a
pre-processing step that splits the file, rather than a DAG variant.

**Saves:** ~150 LOC in dag.py, removes an entire class of DAG shapes.

### 5b. Collapse align into transcribe

Every modern transcriber (Whisper, Parakeet, Riva) produces word-level
timestamps. The separate phoneme alignment stage exists for legacy Whisper
models with inaccurate attention-based timestamps. If you accept
attention-based timestamps (or require engines that produce accurate ones),
you can eliminate the align stage entirely.

**Saves:** An entire pipeline stage, ~200 LOC in dag.py + selector,
one engine directory, one docker service.

### 5c. Make PII detection a post-processing hook, not a pipeline stage

PII detection and audio redaction are the only stages that don't improve
the transcript -- they're compliance features. Making them a post-merge
webhook or async job (rather than pipeline stages that block the merge)
simplifies the core pipeline without losing the feature.

**Saves:** Two pipeline stages from the DAG builder, ~100 LOC.

### 5d. Single registry protocol

Replace `BatchEngineRegistry` (sync) and `WorkerRegistry` (async) with a
single async `EngineRegistry`. All engines register the same way. The
registry stores capabilities including whether the engine accepts streaming.

**Saves:** ~400 LOC of duplicate registry code.

---

## 6. Recommended Execution Order

1. **Unify the engine SDK** -- single base class with optional streaming.
   This is the highest-impact change and unblocks everything else.

2. **Unify the registry** -- single registration protocol for all engines.
   Quick win after SDK unification.

3. **Make pipeline stages declarative** -- engine.yaml declares
   inputs/outputs. DAG builder resolves from declarations.
   Necessary before adding new stages.

4. **Collapse session router into orchestrator** -- unified engine
   discovery and allocation.

5. **Extract WebSocket proxy core** -- reduce gateway WS duplication.

6. **Add new stages** (VAD, emotion, etc.) -- now trivial with
   declarative pipeline.

---

## 7. Risk Assessment

| Change | Risk | Mitigation |
|---|---|---|
| SDK unification | High -- touches every engine | Feature-flag: engines can opt into unified mode gradually |
| Registry unification | Low -- internal protocol | Run old + new in parallel, cut over |
| Declarative pipeline | Medium -- changes orchestrator core | Keep hardcoded path as fallback during transition |
| Session router merge | Medium -- affects realtime latency | Benchmark allocation latency before/after |
| Gateway WS refactor | Low -- protocol adapters are well-defined | Contract tests per API compatibility layer |
