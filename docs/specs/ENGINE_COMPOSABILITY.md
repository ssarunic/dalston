# Engine Interfaces & Composability

> **Status:** Draft
> **Date:** March 2026
> **Author:** Saša Sarunić
> **Audience:** Internal / Contributors

---

## 1. Executive Summary

Dalston is an open-source, self-hosted audio intelligence platform. Its core
architectural bet is that speech processing pipelines should be composable from
independent, pluggable engines rather than locked into monolithic vendor stacks.

This document defines the direction for how engines expose their capabilities
(the engine interface contract), how multiple engines can be composed into
higher-order units (composability), and how third-party runtimes like NVIDIA
NIM/Riva integrate without compromising Dalston's architectural independence.

**The key principle is: uniform interface at every level of the tree.** Whether
a unit of work is handled by a single ONNX model, a multi-stage NIM container,
or a Dalston-composed pipeline of three separate engines, the orchestrator sees
the same contract: declared capabilities in, structured results out.

---

## 2. Context & Motivation

### 2.1 Current State

Dalston runs a capability-gated pipeline where the DAG builder (`dag.py`)
dynamically skips stages based on engine capabilities. A transcriber declaring
`supports_word_timestamps: true` causes the alignment stage to be elided; one
declaring `includes_diarization: true` causes the diarisation stage to be
skipped. This works well for simple cases but has limitations:

- **Monolithic assumptions.** The pipeline assumes each stage is handled by
  exactly one engine. There is no formal way to express that a single engine
  covers multiple stages, or that multiple engines should be composed to cover
  a single logical unit of work.

- **NIM integration gap.** NVIDIA NIM containers bundle transcription,
  diarisation, timestamps, and punctuation behind a single gRPC call. Dalston's
  Riva engine driver (`engines/stt-unified/riva/`) already wraps this, but the
  system has no clean way to represent a NIM container as a single engine that
  happens to cover many stages. Today, each NIM deployment is registered as a
  `stage: transcribe` engine with boolean capability flags
  (`includes_diarization`, `supports_word_timestamps`), and the DAG builder
  special-cases these flags to skip downstream stages. This works but doesn't
  generalise.

- **No composition primitive.** If a user wants "Meeting mode" (Parakeet for
  transcription + pyannote for diarisation + PII redaction), that composition
  is implicit in orchestrator logic rather than declared as a reusable
  configuration.

- **Fixed result envelope.** The current `MergeResponse` has fixed top-level
  fields (`text`, `segments`, `speakers`, `pii_entities`). Adding a new stage
  (emotion detection, topic segmentation, summarisation) requires changing the
  envelope schema and touching every consumer. This doesn't scale.

### 2.2 Design Goals

1. Any engine, whether a leaf or a composite, presents the same interface to
   the orchestrator.
2. The orchestrator does not need to know whether a capability comes from one
   container or five.
3. NIM, Riva, and future vendor runtimes slot in as engine types without
   requiring Dalston to adopt their API surface as its own.
4. Capability Profiles (e.g. "Fast English," "Multilingual," "Meeting,"
   "Compliance") become named composite configurations defined by the
   operator, not orchestrator-level logic. Dalston provides the mechanism;
   the operator defines which profiles exist for their deployment.
5. The architecture supports both batch and streaming modes, with composites
   initially targeting batch only.
6. The result envelope is extensible by design — adding a new stage type does
   not change the envelope schema.

---

## 3. The Engine Interface Contract

Every engine in Dalston — regardless of implementation — adheres to a single
interface contract. This is the foundational invariant that makes composability
possible.

### 3.1 Engine Card (`engine.yaml`)

Each engine declares its identity, capabilities, and operational metadata in an
engine card. The card is the primary source of truth the orchestrator uses for
routing and scheduling, reconciled at runtime with the capability introspection
endpoint.

Dalston's current engine.yaml schema (v1.1) already covers most of this. The
additions are `stages` (plural, for multi-stage engines) and `languages`:

```yaml
# Leaf engine — single-stage
name: parakeet-tdt-0.6b-v3
type: onnx-asr
version: 1.0.0

capabilities:
  stages: [transcription, alignment]
  languages: [en]
  streaming: vad-wrapped
  max_audio_duration: 7200

hardware:
  gpu_required: true
  min_vram_gb: 1.2

performance:
  rtf_gpu: 0.03
  rtf_cpu: null

interface:
  protocol: dalston-native
  health: /health
  submit: /v1/transcribe
  result: /v1/result/{job_id}
```

**Reconciliation rule:** The engine card declares *potential* capabilities at
build time. The capability introspection endpoint (see 3.2) returns *actual*
capabilities at runtime, which may be a subset (e.g., a NIM container deployed
without the Sortformer diarisation profile). When the two disagree, the runtime
introspection result is authoritative. The card is used for scheduling when the
engine is not yet running.

### 3.2 Interface Operations

Regardless of engine type, every engine must support these operations:

| Operation               | Description                                                 | Required |
|-------------------------|-------------------------------------------------------------|----------|
| Health check            | Returns engine readiness, loaded model(s), available VRAM   | Yes      |
| Submit job              | Accepts audio + config, returns job ID (async) or result (sync) | Yes  |
| Retrieve result         | Returns structured result for a given job ID                | Yes (async engines) |
| Capability introspection| Returns runtime capabilities (actual, not just declared)    | Yes      |
| Cancel job              | Cancels an in-progress job                                  | Optional |
| Stream                  | Bidirectional audio streaming with partial results          | Optional |

These map directly to Dalston's existing engine SDK operations:
`Engine.process()` (submit), `Engine.health_check()` (health),
`Engine.get_capabilities()` (introspection). M79 exposes these as HTTP
endpoints on leaf engines (`/health`, `/v1/capabilities`,
`/v1/transcribe`). M80 builds on this by having the orchestrator push
work to engines via those endpoints.

### 3.3 Structured Result Format

Every engine returns results in a **stage-keyed envelope**. Instead of fixed
top-level fields, results are keyed by the stage that produced them. This
means adding a new stage (emotion detection, summarisation) never changes the
envelope schema — it just adds a new key.

```json
{
  "job_id": "abc-123",
  "status": "completed",
  "stages_completed": ["transcription", "alignment"],
  "results": {
    "transcription": {
      "text": "Hello, how are you?",
      "segments": [
        {
          "start": 0.0,
          "end": 1.8,
          "text": "Hello, how are you?",
          "words": [
            {
              "text": "Hello",
              "start": 0.0,
              "end": 0.42,
              "confidence": 0.97
            }
          ]
        }
      ],
      "language": "en",
      "language_confidence": 0.99
    },
    "alignment": {
      "skipped": true,
      "skip_reason": "transcriber produced word-level timestamps"
    }
  },
  "engine": "parakeet-tdt-0.6b-v3",
  "duration_ms": 1240
}
```

**Key differences from the current `MergeResponse`:**

- `results` is a `dict[str, StageResult]` — each key is a stage name, each
  value is that stage's typed output model (the existing `Transcript`,
  `AlignmentResponse`, `DiarizationResponse`, `PIIDetectionResponse`, etc.).
- Stages that were not processed are either absent from the dict or present
  with `{"skipped": true, "skip_reason": "..."}`.
- The orchestrator's `assemble_transcript()` merges results from successive
  stages into the final `MergeResponse` for API consumers. The envelope
  itself is an internal contract between engines and the orchestrator.

**Why not keep fixed fields?** Dalston's pipeline is growing. PII detection
and audio redaction are already partially deferred to post-processing. Future
stages (emotion detection, topic segmentation, summarisation, translation)
would each require a schema change to the envelope. A stage-keyed map is
extensible by default: new stage, new key, zero envelope changes.

**Compatibility:** The existing `pipeline_types.py` models (`Transcript`,
`AlignmentResponse`, `DiarizationResponse`, etc.) remain as-is. They become
the value types within the stage-keyed results dict. The current
`assemble_transcript()` function continues to produce `MergeResponse` for
the API layer — nothing changes for API consumers.

---

## 4. Engine Types

Dalston supports multiple engine types, each with its own runtime driver that
translates between the engine's native protocol and Dalston's interface
contract.

| Engine Type      | Runtime                  | Protocol to Engine | Coverage                          |
|------------------|--------------------------|--------------------|-----------------------------------|
| `onnx-asr`       | ONNX Runtime             | In-process Python  | Parakeet/Conformer CTC, TDT       |
| `faster-whisper`  | CTranslate2              | In-process Python  | Whisper family                     |
| `hf-asr`          | HuggingFace transformers | In-process Python  | Any HF ASR model                   |
| `vllm-asr`        | vLLM                     | In-process Python  | Audio LLMs (Qwen2-Audio, etc.)    |
| `diarize-pyannote` | pyannote.audio          | In-process Python  | Speaker diarisation                |
| `nim-riva`        | NVIDIA NIM container     | gRPC (Riva protos) | NIM-packaged models                |
| `composite`       | Dalston orchestration    | Internal dispatch  | Multi-engine composition           |

### Key Design Decision: Protocol Translation, Not Protocol Adoption

Each engine type has a driver that speaks the engine's native protocol (gRPC
for NIM, in-process Python for ONNX/vLLM/pyannote) and translates to/from
Dalston's structured result format. Dalston does not adopt any vendor's
protocol as its own API surface.

This is already the pattern in the codebase. The Riva engine driver
(`engines/stt-unified/riva/riva_client.py`) speaks gRPC to the NIM container
and translates Riva's `StreamingRecognitionResult` into Dalston's `Transcript`
type. The faster-whisper driver does the same via in-process Python. The
pattern simply needs to be formalised and extended.

**Why not adopt Riva's API as primary?** Riva's API assumes a monolithic
pipeline behind a single gRPC service. Its `RecognitionConfig` cannot express
multi-engine routing, per-request engine selection, async job lifecycle, or
pipeline customisation. Adopting it as primary would require non-standard
extensions, creating "Riva with proprietary extras" — the worst of both
worlds. Vendor protocols are consumed downstream (engine drivers) and
optionally offered upstream (compatibility shims), but the Dalston Native API
remains the primary interface.

### 4.1 The `nim-riva` Sidecar

A NIM container is an opaque GPU workload with its own constraints (25 MB
upload limit, synchronous-only processing, minimal HTTP API, gRPC required
for full feature access). Rather than encoding these constraints in the
orchestrator, Dalston wraps each NIM container in a thin **sidecar** — a
CPU-only container that exposes the standard Dalston engine interface and
handles all NIM-specific concerns internally.

This follows the same topology established in M72 for faster-whisper and
parakeet: the GPU model server is a black box, and a lightweight adapter
speaks its native protocol while presenting a uniform Dalston API externally.

```
┌─────────────────────────────────────────────────┐
│  nim-riva sidecar (CPU, Dalston interface)       │
│                                                   │
│  GET  /health          ─── health ──▶ NIM :9000  │
│  GET  /v1/capabilities ─── introspect ──▶ NIM    │
│  POST /v1/transcribe   ─── gRPC ──▶ NIM :50051  │
│                                                   │
│  Handles internally:                              │
│  • S3/MinIO download (NIM needs audio bytes)      │
│  • Large file chunking (NIM's 25 MB limit)        │
│  • Sync-to-async bridging                         │
│  • gRPC parameter mapping                         │
│  • Result translation to Dalston envelope         │
└──────────────────────────┬────────────────────────┘
                           │ gRPC (Riva protos)
                    ┌──────▼──────┐
                    │ NIM container│
                    │ (GPU)        │
                    │ :50051 gRPC  │
                    │ :9000  HTTP  │
                    └─────────────┘
```

The sidecar's engine card declares the NIM container's capabilities:

```yaml
name: nim-parakeet-ctc-1.1b-sortformer
type: nim-riva
version: 1.0.0

capabilities:
  stages: [transcription, alignment, diarisation, punctuation]
  languages: [en]
  streaming: native

hardware:
  gpu_required: true
  min_vram_gb: 4

runtime:
  image: nvcr.io/nim/nvidia/parakeet-1-1b-ctc-en-us:latest
  grpc_port: 50051
  http_port: 9000
  nim_profile: "parakeet-1-1b-ctc-en-us-ofl-true"

interface:
  protocol: dalston-native
  health: /health
  submit: /v1/transcribe
  capabilities: /v1/capabilities
```

Note that `interface.protocol` is `dalston-native`, not `nim-riva`. The
sidecar absorbs the protocol translation — the orchestrator sees a standard
Dalston engine, not a NIM container.

**Parameter mapping (internal to sidecar):**

| Dalston Parameter        | Riva Mapping                           | Notes                               |
|--------------------------|----------------------------------------|--------------------------------------|
| `language`               | `language_code`                        | BCP-47 tag                           |
| `enable_timestamps`      | `enable_word_time_offsets`             | Boolean                              |
| `enable_diarisation`     | `SpeakerDiarizationConfig.enable`      | Requires Sortformer profile          |
| `enable_punctuation`     | `enable_automatic_punctuation`         | Boolean                              |
| `verbatim`               | `verbatim_transcripts`                 | Disables ITN when true               |
| `vocabulary`             | `SpeechContext` phrases + boost scores | Word boosting via flashlight decoder |
| `custom`                 | `custom_configuration`                 | Pass-through key-value pairs         |

**Dynamic capability detection:** A NIM container's actual capabilities depend
on which deployment profile was loaded at startup (e.g., `parakeet-ctc-en-us`
with `--speaker-diarization` vs. without). The sidecar's `/v1/capabilities`
endpoint queries the NIM container at runtime and returns only what's actually
available. The engine card declares the superset; runtime introspection is
authoritative (reconciliation rule from §3.1).

---

## 5. Composability

Composability is the mechanism by which multiple engines are packaged as a
single logical unit. The composite pattern applies recursively: a composite
can contain other composites.

### 5.1 The Composite Engine

A composite engine is declared in an engine card with `type: composite`. It
lists its constituent engines and the stages each one handles. The composite's
declared capabilities are the union of its children's capabilities.

```yaml
name: meeting-english
type: composite
version: 1.0.0

capabilities:
  stages: [transcription, alignment, diarisation, pii_detection]
  languages: [en]
  streaming: false              # composites are batch-only in v1

compose:
  - engine: parakeet-tdt-0.6b-v3
    stages: [transcription, alignment]
  - engine: pyannote-4.0
    stages: [diarisation]
  - engine: pii-presidio
    stages: [pii_detection]

pipeline:
  mode: parallel
  parallel: [transcription, diarisation]
  sequential_after: [pii_detection]
  error_strategy: partial_result
```

### 5.2 Execution Modes

**Sequential (default).** Stages execute in the declared order. Each stage
receives the accumulated result from prior stages. This is the simplest mode
and matches the current DAG execution model.

**Parallel with sequential tail.** Independent stages that don't depend on
each other's output run concurrently. In the example above, transcription
(with alignment) and diarisation both take audio as input and don't depend
on each other, so they run in parallel. PII detection needs both
transcription text and speaker labels, so it waits for both to complete.

```
              ┌──▶ parakeet-tdt ──────┐
audio ────────┤    (transcription,    ├──▶ pii-presidio ──▶ merge
              │     alignment)        │    (pii_detection)
              └──▶ pyannote-4.0 ─────┘
                   (diarisation)
```

This is a meaningful optimisation without the complexity of full DAG execution
inside composites. The orchestrator already knows how to fan out parallel tasks
(see per-channel pipelines in `dag.py`). The composite engine reuses that
machinery.

### 5.3 Composition Rules

**Stage coverage.** Within a composite, each stage must be covered by exactly
one child. If two children declare the same stage, it is a configuration error.
This is a v1 simplification that will be relaxed in future versions (see §10,
Open Questions) to support A/B testing and fallback chains.

**Capability union.** The composite's declared `stages` are exactly the union
of its children's stages. The composite does not invent capabilities.

**Partial results on failure.** If a non-critical stage fails (e.g.
diarisation), the composite returns the result from completed stages with a
clear status indicating which stages succeeded and which failed. Critical
stages (transcription) cause full failure. Criticality is determined by the
`required` field on each task, matching the existing DAG model.

**Recursive composition.** A composite can reference another composite as a
child. The orchestrator resolves the tree at scheduling time, flattening it
into a concrete task DAG.

### 5.4 How the Orchestrator Sees It

To illustrate the uniform interface principle, consider three engines that all
declare the same capabilities:

| Engine                      | Type        | Stages Declared                              | Internal Implementation           |
|-----------------------------|-------------|----------------------------------------------|-----------------------------------|
| `nim-parakeet-sortformer`   | `nim-riva`  | transcription, alignment, diarisation        | Single NIM container               |
| `meeting-english`           | `composite` | transcription, alignment, diarisation, pii   | Parakeet + pyannote + presidio     |
| `whisper-pyannote`          | `composite` | transcription, alignment, diarisation        | faster-whisper + wav2vec2 + pyannote |

The orchestrator treats all three identically. It sees engines that can handle
a set of stages. Selection is based on capability match, language support,
latency requirements, and resource availability — not on whether the engine is
a leaf or a composite.

### The Tree Analogy

Think of Dalston's engine architecture as a tree. Leaf nodes are individual
engines (ONNX, faster-whisper, NIM, pyannote). Branch nodes are composites
that group leaves. The root is the job request. At every level of the tree,
the interface is the same: capabilities declared, audio in, structured result
out. The orchestrator only interacts with the root-level engine for a given
job — whether that root is a leaf or a branch is an implementation detail.

---

## 6. Resource Scheduling for Composites

Resource scheduling for composites is a critical-path problem, not an
afterthought. When a composite's children need separate GPUs (e.g. Parakeet
on GPU 0, pyannote on GPU 1), the orchestrator must handle allocation
atomically to prevent deadlocks.

### 6.1 The Deadlock Problem

If composite A holds GPU 0 waiting for GPU 1, and composite B holds GPU 1
waiting for GPU 0, both are deadlocked. This cannot be solved by "just
checking availability first" because the check-then-act is inherently racy.

### 6.2 Solution: All-or-Nothing Reservation

When the orchestrator expands a composite into child tasks at scheduling time,
it acquires all required resources atomically:

1. **Flatten the composite** into its leaf engine requirements (with resource
   needs from each engine card).
2. **Check aggregate availability** — all required GPUs, VRAM, and CPU must
   be available simultaneously.
3. **Reserve atomically** (Redis transaction) or **queue the composite** for
   later if resources are insufficient.
4. **Execute children** with guaranteed resources. Sequential children release
   resources as they complete; parallel children hold theirs concurrently.

This maps to how the existing orchestrator handles multi-task jobs: tasks with
unmet dependencies are PENDING until their prerequisites complete. The
difference is that composite resource reservation adds a resource-availability
gate alongside the data-dependency gate.

### 6.3 Sequential Resource Release

For sequential composites (the default), an optimisation: each child releases
its resources upon completion, and the next child's resources are acquired
just-in-time. This avoids holding all GPUs for the entire composite duration.
The trade-off is that a later child might fail to acquire its resources after
an earlier child has already run. The `error_strategy: partial_result` handles
this: the composite returns what it has so far.

---

## 7. Capability Profiles

Capability Profiles are named, user-facing composite configurations. They
abstract engine selection into intent-driven choices.

**Dalston ships no built-in profiles.** Profiles are entirely defined by the
operator running the infrastructure — the list of profiles, the engines they
map to, and the default parameters are all deployment-specific choices. This
is a convenience mechanism, not a curated catalog. The examples below
illustrate what profiles *could* look like, not what Dalston provides
out of the box.

| Profile (example)  | Intent                             | Resolved Engine(s)              | Key Characteristics                          |
|---------------|------------------------------------|---------------------------------|----------------------------------------------|
| `fast-english` | Low-latency English transcription | `parakeet-tdt-0.6b-v3` (leaf) | ONNX, sub-second RTF, no diarisation         |
| `meeting`      | Multi-speaker meeting transcription | `meeting-english` (composite) | Transcription + diarisation + PII            |
| `multilingual` | Best quality, any language        | `nim-parakeet-rnnt-1.1b` (leaf) | NIM, 25+ languages, streaming              |
| `compliance`   | Regulated industry pipeline       | `compliance-full` (composite) | Meeting + PII + audit logging                |

Profiles are defined as simple mappings, not new engine types:

```yaml
profiles:
  fast-english:
    engine: parakeet-tdt-0.6b-v3
    defaults:
      enable_timestamps: true
      enable_punctuation: true

  meeting:
    engine: meeting-english
    defaults:
      enable_diarisation: true
      enable_timestamps: true
      enable_pii_detection: true
```

The Dalston API accepts a `profile` parameter on job submission. The
orchestrator resolves it to the corresponding engine and applies the default
configuration. **Client overrides win** — if a client specifies
`profile=meeting` but `enable_pii_detection=false`, the PII stage is skipped.
This is a shallow merge: client parameters override profile defaults at the
top level.

---

## 8. API Strategy

Dalston exposes multiple API surfaces that coexist without conflict. The
principle is: own the primary, offer compatibility as shims.

### 8.1 Three-Tier API Architecture

**Tier 1: Dalston Native API (Primary)**

A RESTful API that fully expresses Dalston's capabilities: async job
submission, pipeline configuration, engine routing, capability introspection,
and composite management. This is where Dalston's differentiation lives.

- Job lifecycle: `POST /v1/jobs` (submit) → `GET /v1/jobs/{id}` (poll) →
  `GET /v1/jobs/{id}/result` (retrieve)
- Engine introspection: `GET /v1/engines` (list) → `GET /v1/engines/{name}`
  (card details, runtime capabilities)
- Profile selection: `POST /v1/jobs` with `profile` parameter resolves to
  engine + defaults
- Pipeline customisation: Override stages, choose specific engines, skip steps

**Tier 2: OpenAI-Compatible API (Compatibility)**

Already implemented. Covers the common case of "POST a file, get text back."
Maps to Dalston's native job pipeline internally. Provides drop-in
compatibility for applications built against OpenAI's Whisper API or any
OpenAI-compatible provider.

- Endpoint: `POST /v1/audio/transcriptions`
- Parameters: `file`, `model`, `language`, `response_format`

**Tier 3: Riva gRPC Compatibility (Optional, Future)**

A thin adapter that accepts `RivaSpeechRecognition` gRPC calls on port 50051
and translates them into Dalston native jobs. This enables migration from
NIM/Riva deployments: swap the endpoint URL, everything else works.

- **Value proposition:** "If you're already using NIM, point your client at
  Dalston. Same proto, but now you get engine choice and pipeline
  customisation via the native API when you're ready."
- **Scope:** `Recognize`, `StreamingRecognize`,
  `GetRivaSpeechRecognitionConfig` RPCs only. Not NLP or TTS services.

---

## 9. Streaming Considerations

Streaming adds significant complexity to composability. The initial approach is
pragmatic:

- **Native streaming.** Some engines support streaming natively (NIM,
  faster-whisper in streaming mode). The Dalston driver exposes the engine's
  stream directly. The session router connects the client's WebSocket to the
  engine's stream via the existing `realtime_sdk` infrastructure.

- **VAD-wrapped streaming.** Engines that don't support native streaming get
  real-time capability via Dalston's VAD-based streaming wrapper. The wrapper
  segments incoming audio by silence detection (VAD) and maximum chunk
  duration, transcribes each utterance as a batch call to the engine, and
  streams partial results back to the client. This is a first-class streaming
  mode — every engine supports real-time, the question is only whether the
  engine handles the stream itself or Dalston handles it.

- **Engine card declaration.** An engine declares `streaming: native` if it
  handles streaming internally, or `streaming: vad-wrapped` if it relies on
  the Dalston wrapper. Every engine is one or the other — there is no
  non-streaming case. The session router uses this to determine connection
  strategy (direct passthrough vs. VAD segmentation).

- **Composites are batch-only initially.** Coordinating chunk-level handoffs
  between sub-engines in a composite (e.g. feeding partial transcription
  results to a diariser in real time) is a hard problem. The first version of
  composites operates in batch mode only. A composite that receives a
  streaming request falls back to batch mode with a clear indication to the
  client. Streaming composites are a future milestone requiring a chunked
  coordination protocol.

---

## 10. Implementation Roadmap

The implementation builds bottom-up: start with individual engine containers,
prove the interface contract works in practice, then expand horizontally to
other engine types, and only then vertically into composability and
orchestration. Each layer is validated before the next one begins. This
matters not just for risk management but because hands-on experience with
each component informs whether the interface needs tweaking before it
becomes load-bearing for the layers above.

### Layer 1: Core Engine APIs (transcription + diarisation)

Start with the minimum set of engines needed to prove the interface contract
*and* to attempt composition: two transcription runners and one diarisation
engine. Three engines is enough to validate the contract across different
stage types and to have real inputs for Layer 2.

**Step 1a: `onnx-asr` engine API**

Add HTTP endpoints to the existing Parakeet ONNX engine container:

- `GET /health` — readiness, loaded model, available resources
- `GET /v1/capabilities` — runtime capability introspection (stages,
  languages, streaming support). Returns actual capabilities, not just
  what the engine card declares.
- `POST /v1/transcribe` — accepts audio + config, returns structured
  result with the stage-keyed envelope format from §3.3
- Engine card (`engine.yaml`) updated to declare `stages: [transcription,
  alignment]` and `languages: [en]`

**Step 1b: `faster-whisper` engine API**

Same contract, different runner. The faster-whisper engine gets the
identical HTTP surface:

- Same endpoints: `/health`, `/v1/capabilities`, `/v1/transcribe`
- Engine card declares `stages: [transcription]` (or
  `[transcription, alignment]` if word timestamps are enabled)
- The result format is identical to 1a — the orchestrator cannot tell
  which runner produced the output

**Step 1c: `diarize-pyannote` engine API**

First non-transcription engine. Validates that the interface contract and
stage-keyed result format work for a fundamentally different output type
(speaker segments, not text).

- Same HTTP endpoints as 1a/1b
- Engine card: `stages: [diarisation]`
- The result envelope contains a `diarisation` key with speaker segments,
  not a `transcription` key — this is where the stage-keyed design proves
  its worth

**Why these three:** They are the minimum set that exercises the contract
across different stage types (transcription vs. diarisation) and different
runners (ONNX vs. CTranslate2 vs. pyannote). More importantly, they are
the exact engines needed to build the first composite in Layer 2. We don't
need to API-ify the entire fleet before testing composition — we just need
enough working pieces to snap together.

**Validation gate:** All three engines pass the same integration test
suite — submit audio, get back a stage-keyed result, verify capability
introspection matches actual behaviour. The test suite becomes the
executable specification for all future engines.

---

### Layer 2: First Composite

With transcription and diarisation engines working, compose them into a
single logical unit. This is where we find out whether the interface
contract actually composes — before committing to API-ifying every
remaining engine.

**Step 2a: Composite engine card and validation**

- Define the composite engine card schema (`type: composite`, `compose`
  block listing children and their stages)
- Validate: no stage duplication, capability union matches children,
  all referenced engines exist
- No execution yet — just the declaration and validation layer

**Step 2b: Parallel composite execution**

- The first composite is `meeting-english`: Parakeet (transcription +
  alignment) and pyannote (diarisation) running in parallel, since
  neither depends on the other's output
- The orchestrator resolves the composite into a concrete task sub-graph
- Result merging: accumulate stage-keyed results across children into
  a single envelope
- Resource scheduling: all-or-nothing reservation for parallel children

**Step 2c: Sequential tail after parallel fan-out**

- Add PII detection as a sequential stage that waits for both
  transcription and diarisation to complete
- This validates the full execution model: parallel fan-out followed
  by a sequential dependency
- Partial result handling on non-critical child failure

**Validation gate:** The composite `meeting-english` (Parakeet + pyannote)
produces the same result format as a hypothetical monolithic engine that
covers the same stages. The orchestrator cannot distinguish a leaf from
a composite. The lessons learned here — what worked, what needed tweaking
in the contract — feed back into the remaining engine APIs.

---

### Layer 3: Horizontal Expansion

With the contract proven end-to-end (individual engines → composite), now
expand to the remaining engine types. The interface is stable; this is
mechanical replication.

**Step 3a: Multi-stage engines (`nim-riva`)**

- The NIM/Riva driver already exists. Formalise its HTTP surface to match
  the contract from Layer 1.
- Engine card declares multiple stages:
  `stages: [transcription, alignment, diarisation, punctuation]`
- The driver's `/v1/capabilities` endpoint returns only the stages
  actually available at runtime (depends on NIM deployment profile)
- gRPC translation happens inside the driver — the external HTTP surface
  is identical to any other engine
- This is where the multi-stage capability declaration (§3.1 reconciliation
  rule) gets tested in practice

**Step 3b: Remaining engines (PII, alignment, merge)**

- Each gets the same HTTP surface
- By this point the pattern is mechanical — the interface contract is
  stable and proven across transcription, diarisation, composites, and
  multi-stage engines

**Validation gate:** Every engine in the fleet passes the same integration
test suite. The orchestrator can query any engine's capabilities and
receive results in the same format.

---

### Layer 4: Orchestration Integration

The orchestrator is the last component to change. By this point, every
engine has a stable API, composites work, and the interface contract is
proven across all engine types.

**Step 4a: Generalised DAG building**

- Replace the current `skip_alignment` / `skip_diarization` boolean
  checks with a single rule: if the selected engine's `stages` list
  covers a downstream stage, elide it
- The DAG builder uses `capabilities.stages` instead of individual flags
- This is a simplification of existing code, not new complexity

**Step 4b: Capability Profiles**

- Profile YAML schema and loader
- `profile` parameter on `POST /v1/audio/transcriptions` and
  `POST /v1/jobs`
- No built-in profiles shipped — operators define profiles for their
  deployment (e.g. `fast-english`, `meeting`, `multilingual`)
- Client parameter overrides (shallow merge, client wins)

**Step 4c: Stage-keyed result envelope in the API**

- The internal stage-keyed envelope (used between engines and
  orchestrator since Layer 1) is optionally exposed in the API
  via a `response_format=detailed` parameter
- The existing `MergeResponse` remains the default API output —
  nothing changes for current consumers

**Step 4d: Riva gRPC compatibility shim (optional, future)**

- Thin gRPC server accepting Riva protocol calls, translating to
  Dalston native jobs
- `Recognize` and `StreamingRecognize` RPCs on port 50051
- Only worth building when there is concrete demand from NIM migration
  users

---

## 11. Non-Goals

- **Streaming composites.** Chunk-level coordination between streaming
  sub-engines is out of scope for the initial implementation. Streaming
  works for leaf engines only.

- **A/B testing and ensemble within composites.** Running multiple engines
  for the same stage (for comparison or voting) requires relaxing the
  "no stage duplication" rule. This is a future extension.

- **Fallback chains.** "Try NIM, fall back to faster-whisper if it fails"
  is a useful pattern but requires retry-at-the-composite-level semantics.
  Deferred.

- **Capability filtering on leaf engines.** When an audio LLM does
  transcription + summarisation + emotion in one forward pass but you only
  want transcription, should there be a way to request a subset? Deferred
  until audio LLM integration matures.

- **Composite-level caching.** If a composite runs transcription +
  diarisation, and a later request only needs transcription from the same
  audio, should the composite cache intermediate results? Not in v1.

---

## 12. Open Questions

1. **Versioning composites.** When a child engine is updated (e.g. pyannote
   4.0 → 5.0), does the composite's version change? Current thinking:
   composites have their own version, children are pinned. Updating a child
   produces a new composite version.

2. **Profile inheritance.** Can profiles extend other profiles
   (`meeting-with-pii extends meeting`)? Current thinking: no, keep it flat.
   A profile is a single mapping, not a chain.

3. **Partial capabilities on multi-stage leaves.** A NIM container declaring
   `stages: [transcription, alignment, diarisation]` but deployed without
   Sortformer only has `[transcription, alignment]` at runtime. The
   reconciliation rule (§3.1) handles this, but should the orchestrator
   automatically fall back to a dedicated diariser for the missing stage?
   Current thinking: yes, the DAG builder should treat runtime capabilities
   as the engine's actual stage coverage and fill gaps with other engines.

4. **Audio LLM composites.** Models like Qwen2-Audio do transcription +
   summarisation + emotion in a single forward pass. They declare many
   capabilities as a leaf. Should they be composable with traditional
   engines? This may require a "capability filter" on engine selection.

---

## 13. Relationship to Other Milestones

- **M80 (Engine Control Plane):** M80 replaces pull-based dispatch with
  push-based typed APIs. The composability work builds on M80's typed
  stage-specific request/response models and push-based dispatch. M80 should
  land first.

- **M79 (Cross-Mode Fleet Scheduler):** M79 adds fleet-level scheduling
  intelligence. Composite resource reservation (§6) extends M79's scheduling
  with all-or-nothing resource acquisition.

- **M31/M36 (Capability-Driven Selection):** The current capability-driven
  DAG building and model selection are the foundation. Multi-stage capability
  declaration (Step 2) generalises the existing `supports_word_timestamps` /
  `includes_diarization` flags into a uniform `stages` list.

- **M64 (Registry Unification):** The unified `EngineRecord` already supports
  multi-interface engines. Composite engines register as a single record
  whose `capabilities.stages` covers the union.
