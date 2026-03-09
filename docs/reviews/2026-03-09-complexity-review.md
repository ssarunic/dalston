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

### 5a. Drop per_channel from the DAG -- model it as a pre-processing split

The `per_channel` mode in `dag.py` creates a completely different DAG shape:
`_build_per_channel_dag_with_engines()` (160 LOC) duplicates the entire
normal pipeline with `_ch{N}` suffixed stages, parallel branches, and
channel-aware merge logic. Every future stage added to the pipeline would
need a per-channel variant too.

**Replace with a pre-processing split at the gateway level:**

1. When `speaker_detection=per_channel`, the gateway splits the stereo file
   into N mono files using FFmpeg (~10ms, trivial).
2. It submits N independent child jobs, each running the normal mono pipeline
   with `speaker_detection=none`. No DAG changes needed.
3. A parent job tracks the children. When all complete, a lightweight stitcher
   merges results: interleave segments by timestamp, label speaker = channel
   index (or `known_speaker_names`).

```
                              ┌─ Job A (ch0.wav): prepare → transcribe → ... → merge
stereo.wav → channel-split ───┤
                              └─ Job B (ch1.wav): prepare → transcribe → ... → merge

Parent job: stitch Job A + Job B results, label speakers by channel
```

**What gets deleted (~275 LOC):**

| Component | LOC |
|---|---|
| `_build_per_channel_dag_with_engines()` | ~160 |
| `per_channel` branches in `_build_dag_with_engines()` | ~15 |
| `_process_split_channels()` in audio-prepare engine | ~50 |
| per-channel merge logic in final-merger (stereo reassembly) | ~40 |
| `_ch{N}` stage name parsing in handlers.py | ~10 |

**What gets added (~110 LOC):**

| Component | LOC |
|---|---|
| `split_channels()` utility (FFmpeg one-liner per channel) | ~20 |
| Parent-child job relationship in gateway | ~50 |
| `stitch_per_channel_results()` post-processor | ~40 |

**Net savings:** ~165 LOC, plus elimination of all future per-channel
stage variants. Scales to N channels with zero DAG changes (current
implementation hard-caps at 2).

**What you give up:** Single job ID atomicity (now parent + N children).
The gateway can hide this from clients by presenting the parent job as
the single status endpoint.

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

### 5d. Eliminate the merge stage entirely

The merge stage (`engines/stt-merge/final-merger/engine.py`, 1141 LOC)
exists because the DAG's parallel branches write **separate artifact
files** with incompatible formats:

- transcribe → `TranscribeOutput` (text, segments, language)
- diarize → `DiarizeOutput` (speaker turns) — separate file
- align → `AlignOutput` (word timestamps) — separate file
- pii_detect → `PIIDetectOutput` (entities) — separate file

Since these stages run in parallel (or at least independently), they
can't write to the same document. Merge combines all of them into the
canonical `transcript.json` (1141 LOC of format conversion, overlap
matching, speaker assignment, PII redaction text splicing, per-channel
stereo audio assembly via FFmpeg, etc.).

**With a linear pipeline, merge is unnecessary.** Each stage reads the
previous stage's output, enriches it, and writes it forward. The
document evolves through the pipeline:

```
prepare    → { audio_meta }
transcribe → { audio_meta, text, segments[], language }
diarize    → { ..., segments[].speaker, speakers[] }
emotion    → { ..., segments[].emotion }
```

The last stage's output IS the final `transcript.json`. No combiner
needed.

**What merge currently does and where it moves:**

| Merge responsibility | Where it goes |
|---|---|
| Combine transcribe + diarize outputs | Diarize stage enriches transcript directly |
| Speaker assignment via overlap matching | Diarize stage does this inline |
| Word-level timestamp merging from align | Eliminated (transcribe produces native timestamps, section 5b) |
| PII entity splicing into text | Post-processing hook (section 5c) |
| Stereo audio assembly via FFmpeg | Pre-processing split handles this (section 5a) |
| `known_speaker_names` remapping | Diarize stage or gateway response formatter |
| Build metadata (pipeline_stages, warnings) | Orchestrator writes metadata on job completion |
| Format canonical `transcript.json` | Each stage writes the same schema; last stage's output is canonical |

**What this requires:** A shared `Transcript` schema that all stages
read and write. Currently each stage has its own output model
(`TranscribeOutput`, `DiarizeOutput`, etc.). These would be replaced
with a single evolving `Transcript` model where each stage populates
its fields.

```python
class Transcript(BaseModel):
    """The single document that flows through the pipeline."""
    job_id: str
    version: str = "1.0"
    metadata: TranscriptMetadata
    text: str = ""
    speakers: list[Speaker] = []
    segments: list[Segment] = []
    # Each stage adds its fields; downstream stages see upstream data

class Segment(BaseModel):
    id: str
    start: float
    end: float
    text: str
    speaker: str | None = None       # populated by diarize
    words: list[Word] = []           # populated by transcribe (native timestamps)
    emotion: str | None = None       # populated by emotion detection
    emotion_confidence: float | None = None
    events: list[Event] = []         # populated by non-verbal detection
```

**Saves:** 1141 LOC (the entire final-merger engine), one Docker
service, one engine directory. Also eliminates the artifact fan-in
pattern in the materializer and the `input_bindings` / `previous_outputs`
plumbing that exists solely to feed merge.

**What you give up:** The ability to run stages in parallel on separate
outputs (already given up by choosing a linear pipeline). Also, stages
are now coupled to a shared schema -- but this is a feature, not a bug.
It makes the contract explicit and enforced by Pydantic, rather than
implicit in merge's 1141-line format conversion logic.

### 5e. Single registry protocol

Replace `BatchEngineRegistry` (sync) and `WorkerRegistry` (async) with a
single async `EngineRegistry`. All engines register the same way. The
registry stores capabilities including whether the engine accepts streaming.

**Saves:** ~400 LOC of duplicate registry code.

---

## 6. Combined Effect: DAG Becomes a Linear Pipeline (Queue)

Applying all simplifications from section 5, the current DAG:

```
              ┌─ transcribe → [align] ─────────────────────┐
prepare ──────┤                                             ├─ [pii_detect] → [audio_redact] → merge
              └─ [diarize] ────────────────────────────────┘
              (+ per_channel variant: entirely different DAG shape)
```

...reduces to a **fully linear pipeline**:

```
prepare → transcribe → [diarize]
```

- **per_channel branching**: gone (pre-processing split, section 5a)
- **align stage**: gone (modern transcribers produce native word timestamps, section 5b)
- **pii_detect + audio_redact**: gone from core pipeline (post-processing hook, section 5c)
- **merge stage**: gone (each stage enriches a shared Transcript document, section 5d)
- **diarize ∥ transcribe parallelism**: dropped (see rationale below)

### Why fully linear over a fork-join

The current DAG runs diarize in parallel with transcribe (both depend
only on prepare). In theory this saves time. In practice:

- **CPU**: You can't run transcribe + diarize in parallel on a single
  machine -- both are compute-intensive and will thrash each other.
  Parallel execution only helps if they're on different instances (cloud).
- **GPU**: Diarization takes seconds. The parallelism saves negligible time.
- **Riva/Parakeet with Sortformer**: NVIDIA's latest models bundle
  transcription + streaming diarization in a single inference call
  (see section 6b). No pipeline parallelism needed -- it's one engine.

A fully linear pipeline means the orchestrator becomes a **simple FIFO
queue processor** with zero dependency tracking:

```python
def build_pipeline(job_params, engines):
    stages = ["prepare", "transcribe"]
    if job_params.get("speaker_detection") == "diarize":
        stages.append("diarize")
    return [make_task(stage) for stage in stages]
```

Compare to the current 700+ LOC `dag.py` + 1141 LOC merge engine. No
dependency graph, no fork-join scheduling, no merge dependency
accumulation, no artifact fan-in. Each stage reads the previous stage's
`Transcript` object, enriches it, and writes it forward. The last
stage's output is the final `transcript.json`.

### What this eliminates from the orchestrator

- `_build_per_channel_dag_with_engines()` (160 LOC)
- `_build_dag_with_engines()` dependency graph construction (~540 LOC)
- Stage-specific `if` branches for align, pii_detect, audio_redact
- Complex merge dependency accumulation
- The concept of "skip_alignment" / "skip_diarization" capability checks
- All dependency resolution logic in the scheduler
- The entire merge engine (1141 LOC) and its Docker service
- The `input_bindings` / `previous_outputs` plumbing in the engine SDK
- Artifact fan-in logic in the materializer

The orchestrator just runs the next stage when the current one completes.

### 6a. Future Stages Keep It Linear

Adding new processing stages does NOT reintroduce DAG complexity. Every
foreseeable stage fits naturally into a linear pipeline because each
depends on the output of the previous:

```
prepare → [noise_removal] → [VAD] → transcribe → [diarize] → [speaker_id] → [emotion] → [non_verbal]
           ↑ pre-processing          core          ↑ post-transcribe enrichments
```

Each stage enriches the same `Transcript` document. The last stage's
output is the final `transcript.json`. No merge needed at any point.

| Future Stage | Position | Depends On | Why Linear |
|---|---|---|---|
| Noise removal | Before transcribe | Audio from prepare | Cleans audio for better transcription |
| VAD (voice activity detection) | Before transcribe | Audio (or denoised audio) | Segments audio into speech regions |
| Speaker fingerprint/ID | After diarize | Diarization segments + audio | Matches speakers to known identities |
| Emotion detection | After transcribe | Transcript segments + audio | Labels segments with emotion |
| Non-verbal events | After transcribe | Audio segments | Detects laughter, cough, applause, etc. |
| LLM cleanup/summarization | After all enrichments | Full transcript | Rewrites, summarizes, formats |

Could emotion and non-verbal run in parallel? Yes, they're independent.
But on a single GPU they'd contend for resources anyway. If you ever
need that parallelism (multi-GPU cloud), you can add a single fork-join
at that point -- but don't build the infrastructure until you need it.

**Key insight: the pipeline is ordered by data dependency, and each
stage enriches the output of the previous. This is inherently sequential.
The `Transcript` document is the single artifact that flows through the
entire pipeline, getting richer at each stage.**

### 6b. Multi-capability Engines Collapse the Pipeline Further

The trend in speech AI is toward models that handle multiple stages in
a single inference call. This makes the pipeline even shorter:

**NVIDIA Riva with Streaming Sortformer:**
- Single gRPC/WebSocket call produces: transcription + word timestamps +
  speaker diarization labels, all in streaming mode.
- VAD is built-in (Silero VAD integrated into the pipeline).
- Effectively collapses `[VAD] → transcribe → diarize` into one engine.
- Currently supports Parakeet-CTC and Conformer-CTC models.
- Streaming diarization is beta, supports up to 8 concurrent requests.
- Sources: [Riva ASR Overview](https://docs.nvidia.com/deeplearning/riva/user-guide/docs/asr/asr-overview.html),
  [Streaming Sortformer](https://developer.nvidia.com/blog/identify-speakers-in-meetings-calls-and-voice-apps-in-real-time-with-nvidia-streaming-sortformer/),
  [Riva Speaker Diarization](https://docs.nvidia.com/deeplearning/riva/user-guide/docs/tutorials/asr-speaker-diarization.html),
  [Riva Realtime WebSocket API](https://docs.nvidia.com/nim/riva/asr/latest/realtime-asr.html)

**Alibaba SenseVoice:**
- Single model produces: transcription + emotion labels + non-verbal
  event tags (laughter, cough, applause, crying, etc.).
- 70ms to process 10 seconds of audio (15x faster than Whisper-Large).
- Collapses `transcribe → [emotion] → [non_verbal]` into one engine.
- Source: [SenseVoice on GitHub](https://github.com/FunAudioLLM/SenseVoice)

**NVIDIA Multitalker Parakeet:**
- Streaming ASR that takes diarization output as context to produce
  speaker-attributed transcripts. No speaker enrollment needed.
- Source: [Multitalker Parakeet on HuggingFace](https://huggingface.co/nvidia/multitalker-parakeet-streaming-0.6b-v1)

With these engines, the actual pipeline for a typical job might be:

```
prepare → riva (covers: VAD + transcribe + diarize)
```

Or with SenseVoice:

```
prepare → sensevoice (covers: transcribe + emotion + non_verbal)
```

The pipeline framework doesn't need to know about internal engine
stages -- it just runs whatever stages the engine.yaml declares. If an
engine covers multiple stages, fewer pipeline steps execute. This is
where the declarative engine.yaml approach (section 5, execution order
step 3) pays off: engines declare what they produce, the pipeline skips
stages already covered.

### 6c. Implications for Real-time Architecture

Riva's streaming Sortformer diarization changes the real-time story too.
Currently, Dalston's real-time mode can only do transcription -- diarization
is batch-only. With Riva as a real-time engine:

- **Real-time diarization becomes possible** via Riva's WebSocket API
  with `speaker_diarization.enable_speaker_diarization: true`.
- The session router can allocate Riva workers that stream back
  speaker-labeled transcripts in real-time.
- This eliminates the need for "hybrid mode" (real-time transcription +
  batch diarization enrichment) for Riva-backed deployments.
- Riva deploys on Kubernetes via Helm chart, fitting the existing
  containerized engine model.

---

## 7. Recommended Execution Order

1. **Replace DAG with linear pipeline + eliminate merge** -- rewrite
   `dag.py` as a simple ordered stage list, introduce a shared
   `Transcript` schema that flows through the pipeline. This is the
   highest-leverage change: ~700 LOC in dag.py + 1141 LOC merge engine
   replaced with ~30 LOC pipeline builder + ~100 LOC shared schema.
   Unblocks everything else by removing the complexity that makes other
   changes scary.

2. **Unify the engine SDK** -- single base class with optional streaming.
   High-impact, touches every engine.

3. **Make engine.yaml declarative** -- engines declare what stages they
   cover (e.g., Riva covers transcribe+diarize+VAD). Pipeline skips
   stages already handled. This is how multi-capability engines like
   Riva and SenseVoice integrate cleanly.

4. **Unify the registry** -- single registration protocol for all engines.
   Quick win after SDK unification.

5. **Collapse session router into orchestrator** -- unified engine
   discovery and allocation. Now more valuable since Riva can do
   streaming diarization (real-time workers gain diarization capability).

6. **Extract WebSocket proxy core** -- reduce gateway WS duplication.

7. **Add new stages** (noise removal, VAD, emotion, non-verbal events) --
   trivial with declarative pipeline. Just add engine.yaml + engine.py,
   declare position in pipeline.

---

## 8. Risk Assessment

| Change | Risk | Mitigation |
|---|---|---|
| SDK unification | High -- touches every engine | Feature-flag: engines can opt into unified mode gradually |
| Registry unification | Low -- internal protocol | Run old + new in parallel, cut over |
| Declarative pipeline | Medium -- changes orchestrator core | Keep hardcoded path as fallback during transition |
| Session router merge | Medium -- affects realtime latency | Benchmark allocation latency before/after |
| Gateway WS refactor | Low -- protocol adapters are well-defined | Contract tests per API compatibility layer |
