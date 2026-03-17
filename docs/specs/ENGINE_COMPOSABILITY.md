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
   "Compliance") become named composite configurations, not orchestrator-level
   logic.
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
additions are `stages` (plural, for multi-stage engines), `quality_tier`, and
`languages`:

```yaml
# Leaf engine — single-stage
name: parakeet-tdt-0.6b-v3
type: onnx-asr
version: 1.0.0

capabilities:
  stages: [transcription, alignment]
  languages: [en]
  quality_tier: high           # draft | standard | high
  streaming: false
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
`Engine.get_capabilities()` (introspection). The control plane API from M80
will formalise these as HTTP endpoints.

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
| `vllm-asr`        | vLLM                     | HTTP (OpenAI-compat)| Audio LLMs (Qwen2-Audio, etc.)    |
| `diarize-pyannote` | pyannote.audio          | In-process Python  | Speaker diarisation                |
| `nim-riva`        | NVIDIA NIM container     | gRPC (Riva protos) | NIM-packaged models                |
| `composite`       | Dalston orchestration    | Internal dispatch  | Multi-engine composition           |

### Key Design Decision: Protocol Translation, Not Protocol Adoption

Each engine type has a driver that speaks the engine's native protocol (gRPC
for NIM, Python for ONNX, HTTP for vLLM) and translates to/from Dalston's
structured result format. Dalston does not adopt any vendor's protocol as its
own API surface.

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

### 4.1 The `nim-riva` Engine Driver

The `nim-riva` driver communicates with NVIDIA NIM containers using their
native Riva gRPC protocol. It handles the translation between Riva's
`RecognitionConfig` and Dalston's structured format.

A NIM container may natively cover multiple stages. The driver reflects this
accurately in the engine card:

```yaml
name: nim-parakeet-ctc-1.1b-sortformer
type: nim-riva
version: 1.0.0

capabilities:
  stages: [transcription, alignment, diarisation, punctuation]
  languages: [en]
  quality_tier: high
  streaming: true

hardware:
  gpu_required: true
  min_vram_gb: 4

runtime:
  image: nvcr.io/nim/nvidia/parakeet-1-1b-ctc-en-us:latest
  grpc_port: 50051
  http_port: 9000
  nim_profile: "parakeet-1-1b-ctc-en-us-ofl-true"

interface:
  protocol: nim-riva
  health: http://localhost:9000/v1/health/ready
```

**NIM-specific constraints the driver handles:**

- **25 MB upload limit.** NIM's HTTP API rejects files exceeding 25 MB. The
  driver must chunk large files or rely on Dalston's `prepare` stage to
  pre-chunk audio. This is transparent to the orchestrator.

- **No URL input.** NIM requires audio bytes in the request body. The driver
  downloads from S3/MinIO before forwarding — same pattern as all other
  Dalston engines.

- **Synchronous processing.** NIM has no async/webhook support. The driver
  blocks during inference, which is fine for Dalston's queue-based
  architecture where each engine worker processes one task at a time.

- **HTTP API is minimal.** NIM's HTTP endpoint only accepts `file` and
  `language` — no `response_format`, no `timestamp_granularities`, no
  `temperature`. Word timestamps, diarisation, and vocabulary boosting require
  the gRPC interface. The driver uses gRPC for all batch processing.

**Parameter mapping:**

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
with `--speaker-diarization` vs. without). The engine card declares the
superset of possible capabilities; the capability introspection endpoint
returns what's actually available. The reconciliation rule from §3.1 applies:
runtime introspection is authoritative.

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
  quality_tier: high
  streaming: false

compose:
  - engine: parakeet-tdt-0.6b-v3
    stages: [transcription, alignment]
  - engine: pyannote-4.0
    stages: [diarisation]
  - engine: pii-presidio
    stages: [pii_detection]

pipeline:
  mode: sequential
  parallelise_after: [transcription, alignment]
  error_strategy: partial_result
```

### 5.2 Execution Modes

**Sequential (default).** Stages execute in the declared order. Each stage
receives the accumulated result from prior stages. This is the simplest mode
and matches the current DAG execution model.

**Parallel fan-out after a gate stage.** Independent stages that share the same
dependency can run concurrently. In the example above,
`parallelise_after: [transcription, alignment]` means diarisation and PII
detection both depend on transcription + alignment output, but not on each
other, so they run in parallel.

```
parakeet-tdt ──────┬──▶ pyannote-4.0  ──┐
(transcription,    │                      ├──▶ merge
 alignment)        └──▶ pii-presidio ────┘
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
quality tier, latency requirements, and resource availability — not on whether
the engine is a leaf or a composite.

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

| Profile       | Intent                             | Resolved Engine(s)              | Key Characteristics                          |
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
    quality_tier: standard
    defaults:
      enable_timestamps: true
      enable_punctuation: true

  meeting:
    engine: meeting-english
    quality_tier: high
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

- **Leaf engines can stream natively.** If a NIM container or a faster-whisper
  engine supports streaming, the Dalston driver exposes it directly. The
  session router connects the client's stream to the engine's stream (via the
  existing `realtime_sdk` infrastructure).

- **Composites are batch-only initially.** Coordinating chunk-level handoffs
  between sub-engines in a composite (e.g. feeding partial transcription
  results to a diariser in real time) is a hard problem. The first version of
  composites operates in batch mode only.

- **Streaming composites are a future milestone.** When required, streaming
  composites will need a chunked coordination protocol. This is explicitly
  deferred.

A streaming-capable leaf engine declares `streaming: true` in its engine card.
The orchestrator uses this to determine whether a given job can be served in
streaming mode. If the selected engine is a composite, the job falls back to
batch mode with a clear indication to the client.

---

## 10. Implementation Roadmap

The implementation follows vertical slices, with each milestone delivering
working end-to-end functionality. Each step is independently deployable.

### Step 1: Stage-Keyed Result Envelope

Introduce the `results: dict[str, StageResult]` pattern alongside the
existing fixed-field output.

**Files modified:**

- `dalston/common/pipeline_types.py` — Add `StageResultEnvelope` model with
  stage-keyed results dict
- `dalston/common/transcript.py` — Update `assemble_transcript()` to also
  produce the stage-keyed envelope internally (dual-write)
- `dalston/engine_sdk/types.py` — Add `stages_completed` field to
  `TaskResponse`

**Deliverables:**

```python
class StageResultEnvelope(BaseModel):
    """Internal result envelope between engines and orchestrator."""
    job_id: str
    status: str  # "completed", "partial", "failed"
    stages_completed: list[str]
    results: dict[str, BaseModel]  # stage name → typed stage output
    engine: str
    duration_ms: int
```

The existing `MergeResponse` continues to be the API-facing output. The
envelope is an internal contract.

---

### Step 2: Multi-Stage Capability Declaration

Extend `EngineCapabilities` and the engine card schema to support engines that
cover multiple stages.

**Files modified:**

- `dalston/engine_sdk/types.py` — `EngineCapabilities` already has `stages:
  list[str]`; add `quality_tier` and `languages` fields
- `dalston/engine_sdk/base.py` — `get_capabilities()` parses the extended
  card fields
- `dalston/orchestrator/engine_selector.py` — Selection considers multi-stage
  engines as covering all declared stages
- `dalston/orchestrator/dag.py` — DAG builder elides stages covered by a
  multi-stage engine (generalise the current `skip_alignment` /
  `skip_diarization` pattern)

**Deliverables:**

The DAG builder uses `capabilities.stages` instead of checking individual
boolean flags. A transcriber declaring `stages: [transcription, alignment,
diarisation]` causes all three downstream stages to be elided, replacing
the current `supports_word_timestamps` / `includes_diarization` special
cases with a single generalised rule.

---

### Step 3: `nim-riva` Engine Driver (Multi-Stage)

Formalise the existing Riva engine as a multi-stage `nim-riva` driver with
full parameter mapping and result translation.

**Files modified:**

- `engines/stt-unified/riva/engine.yaml` — Declare multi-stage capabilities
- `engines/stt-unified/riva/batch_engine.py` — Return results covering all
  stages the NIM container handled (transcription + alignment + diarisation)
- `engines/stt-unified/riva/riva_client.py` — Add parameter mapping for
  diarisation, punctuation, word boosting

**Deliverables:**

- NIM containers register with their actual multi-stage capabilities
- The driver populates the stage-keyed result envelope with all stages it
  handled
- Health check integration via NIM's `/v1/health/ready`
- Runtime capability introspection detects whether diarisation is available
  (Sortformer profile loaded or not)

---

### Step 4: Composite Engine Type

Implement the composite engine type with sequential and parallel-after-gate
execution.

**Files modified:**

- `dalston/orchestrator/composite.py` *(new)* — Composite resolution, tree
  flattening, resource aggregation
- `dalston/orchestrator/dag.py` — DAG builder resolves composites into
  concrete task sub-graphs
- `dalston/orchestrator/scheduler.py` — All-or-nothing resource reservation
  for composite children
- `dalston/common/pipeline_types.py` — Composite engine card schema

**Deliverables:**

- Composite engine cards parsed and validated (no stage duplication, capability
  union correctness)
- Composite resolved to concrete task DAG at scheduling time
- Sequential and `parallelise_after` execution modes
- Result merging: accumulate stage-keyed results across children
- Partial result handling on non-critical child failure
- All-or-nothing resource reservation (or sequential acquire-release for
  sequential composites)

---

### Step 5: Capability Profiles

Profile definition, resolution, and API integration.

**Files modified:**

- `dalston/orchestrator/profiles.py` *(new)* — Profile loading and resolution
- `dalston/gateway/api/v1/transcription.py` — Accept `profile` parameter on
  job submission
- `dalston/orchestrator/engine_selector.py` — Profile resolution feeds into
  engine selection

**Deliverables:**

- Profile YAML schema and loader
- `profile` parameter on `POST /v1/audio/transcriptions` and
  `POST /v1/jobs`
- Built-in profiles: `fast-english`, `meeting`, `multilingual`
- Client parameter overrides (shallow merge, client wins)
- Console UI: profile selector on job submission page

---

### Step 6: Riva gRPC Compatibility Shim (Optional, Future)

A thin gRPC server that accepts Riva protocol calls and translates them to
Dalston native jobs.

**Files modified:**

- `dalston/gateway/riva_shim/` *(new)* — gRPC server, request/response
  translation
- Proto files for `RivaSpeechRecognition` service

**Deliverables:**

- `Recognize` and `StreamingRecognize` RPCs
- Translation from `RecognitionConfig` to Dalston native job parameters
- Response translation back to Riva's response protos
- Port 50051 alongside the existing HTTP gateway on 8000

---

## 11. Quality Signals for Engine Selection

The current engine selector (`engine_selector.py`) routes based on capability
match, language support, and engine availability. Missing: a quality signal
that lets the orchestrator prefer a better engine when multiple candidates
match.

### 11.1 Quality Tiers

Each engine card declares a `quality_tier`:

| Tier       | Meaning                                    | Example                        |
|------------|--------------------------------------------|--------------------------------|
| `draft`    | Fastest, lowest resource, acceptable quality | faster-whisper tiny/base      |
| `standard` | Good balance of speed and accuracy          | faster-whisper medium/large-v3 |
| `high`     | Best accuracy, higher resource cost         | Parakeet TDT 1.1b, NIM        |

The API accepts a `quality` parameter (or inherits it from a profile). The
engine selector filters and ranks candidates by tier. If no tier is specified,
`standard` is the default.

### 11.2 Why Not Benchmark Scores?

WER scores are dataset-dependent, language-dependent, and change with model
versions. A static WER number in the engine card would be perpetually stale.
Quality tiers are coarse but honest — they reflect the engine author's
judgment about where the engine sits in the speed/quality tradeoff, and they're
easy to maintain.

---

## 12. Non-Goals

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

## 13. Open Questions

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

## 14. Relationship to Other Milestones

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
