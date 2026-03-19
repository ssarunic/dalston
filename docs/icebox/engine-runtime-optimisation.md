# Engine Runtime Optimisation

|              |                                                                                   |
| ------------ | --------------------------------------------------------------------------------- |
| **Idea**     | Background-optimise ONNX inference sessions per GPU, cache artefacts to S3        |
| **Priority** | Medium — meaningful latency wins, no blocking deployment changes                  |
| **Status**   | Icebox                                                                            |

## Thesis

Dalston's ONNX Runtime path gives us portable, good-enough inference out of
the box — no TensorRT build step, no GPU-locked artefacts, fast cold starts.
But we're leaving performance on the table. ONNX Runtime has optimisation
capabilities (graph fusion, INT8 quantisation, IO binding, TensorRT backend)
that we don't currently exploit, and the gap widens on high-throughput
deployments where per-request latency compounds.

The core idea: **start serving immediately with generic ONNX, then
background-optimise for the specific GPU and hot-swap the inference session**.
This gives us NVIDIA NIM-class performance without NIM's 30-minute blocking
cold starts or combinatorial prebuilt-engine problem.

## Problem

Today, `OnnxModelManager._load_model()` loads a model with default ONNX
Runtime settings (CUDAExecutionProvider or CPUExecutionProvider), optional INT8
quantisation via `DALSTON_QUANTIZATION`, and that's it. Every request on every
GPU gets the same inference path.

This means:

- No graph-level optimisation (attention fusion, constant folding beyond
  defaults, node elimination)
- No hardware-specific tuning (TensorRT acceleration on NVIDIA, execution
  provider configuration per GPU arch)
- CPU↔GPU memory copies between pipeline stages that could be avoided
- Same precision for all workloads regardless of accuracy requirements

## Proposed Approach

### Two-Phase Startup

**Phase 1 — Generic (current behaviour, immediate):** Engine starts, loads ONNX
model with CUDAExecutionProvider, declares itself ready. Serves traffic within
seconds. This is what we do today.

**Phase 2 — Background optimisation (new):** While serving traffic, a background
task:

1. Profiles the GPU (architecture, VRAM, driver version)
2. Runs ONNX graph optimisation (attention fusion, constant folding, node
   elimination)
3. Optionally builds a TensorRT engine for the detected GPU
4. Hot-swaps the `InferenceSession` behind the model handle
5. Persists optimised artefacts to S3, keyed by GPU architecture

Subsequent requests use the faster session. Next cold start on the same GPU
type skips straight to the optimised path.

### Optimisation Techniques

**Graph optimisation** — Run `onnxruntime.transformers.optimizer` or `onnxsim`
as a pre-processing step on Parakeet TDT/CTC models. Fuses attention layers,
eliminates redundant ops, shrinks the graph. Could be baked into model
preparation (one-time cost) or done per-engine at startup.

**Precision calibration** — Beyond the existing `DALSTON_QUANTIZATION=int8` env
var, support calibrated INT8 quantisation using a representative audio dataset.
Expose as capability profiles: e.g., `fast-english-int8` vs
`accurate-english-fp16`. Maps naturally to engine variant YAML.

**IO binding** — ONNX Runtime's `IOBinding` API pre-allocates GPU tensors and
avoids CPU↔GPU copies between pipeline stages. If preparation → transcription →
alignment currently bounces tensors through CPU memory, pinning them on-device
is a meaningful latency win.

**FusedAttention for FastConformer** — Parakeet uses FastConformer with local
attention. ONNX Runtime has a `FusedAttention` operator that can exploit this,
but the exported ONNX graph may fall back to the generic path. Worth verifying
and fixing the export if needed.

**TensorRT backend** — ONNX Runtime's `TensorrtExecutionProvider` can be used
as an optional acceleration layer. Start serving with CUDA EP, build TensorRT
engines in background, switch over when ready. The model stays portable.

### S3 Artefact Cache

Persist optimised sessions to S3 keyed by GPU architecture:

```
models/parakeet-tdt-v3/optimised/sm_75/   # T4 (Turing)
models/parakeet-tdt-v3/optimised/sm_80/   # A100 (Ampere)
models/parakeet-tdt-v3/optimised/sm_86/   # A10G (Ampere)
models/parakeet-tdt-v3/optimised/sm_89/   # L4 (Ada Lovelace)
models/parakeet-tdt-v3/optimised/sm_90/   # H100 (Hopper)
```

On startup, check S3 for a cached artefact matching the detected GPU. If found,
load directly (skip Phase 2). If not, run Phase 2 and upload the result.

This avoids the combinatorial explosion that forces NVIDIA to build on-device:
we only build for GPU architectures we actually deploy to, and the cache grows
organically.

### Engine Card Extension

Extend engine status reporting with optimisation state:

```python
class OptimisationStatus(str, Enum):
    GENERIC = "generic"       # Phase 1 — default ONNX session
    OPTIMISING = "optimising" # Phase 2 — background work in progress
    OPTIMISED = "optimised"   # Phase 2 complete — fast path active
```

The orchestrator could use this for routing — preferring optimised engines for
latency-sensitive work, or spreading load to generic engines during warm-up.

## Why Not Just Use TensorRT / NIM Directly?

The combinatorial space for prebuilt TensorRT engines is larger than it appears:

- **6+ GPU architectures** (T4, A10G, A100, L4, H100, B200) each with
  different sm_XX compute capabilities
- **Driver sensitivity** — TensorRT engines depend on exact CUDA driver and
  TensorRT versions
- **VRAM variations** — A100-40GB vs A100-80GB make different workspace choices
- **Model configurations** — streaming vs offline, batch sizes, max sequence
  lengths, precision modes, optional pipeline stages
- **Calibration coupling** — INT8 calibration tables are invalidated by any
  model weight change

The real matrix is "5 models × 6 GPUs × 3 precisions × 2 modes × N batch
profiles × M driver versions" — hundreds of artefacts to build, test, and
distribute. NVIDIA chose to build on-device rather than maintain that build farm.

Dalston's ONNX-first approach sidesteps this entirely. ONNX models are portable
across all these dimensions by default. We get 80–90% of peak performance
without any combinatorial headache, and close the remaining gap
opportunistically through background optimisation.

## Where This Fits

This builds on existing work:

- **M36 (Runtime Model Management)** — model lifecycle, TTL eviction, the
  `OnnxModelManager` that would gain the optimisation layer
- **M32 (Engine Variant Structure)** — variant YAML could express optimisation
  profiles (e.g., `precision: int8`, `graph_optimised: true`)
- **Engine SDK device detection** — `detect_device()` already handles CUDA
  auto-detection; would be extended with GPU architecture profiling

No dependency on specific milestone completion — this is additive to the current
architecture.

## Open Questions

- **Calibration dataset**: What representative audio samples should we use for
  INT8 calibration? A standard benchmark (LibriSpeech test-clean) or
  production-representative data?
- **Hot-swap safety**: How do we atomically swap the inference session without
  dropping in-flight requests? Reference counting on the model handle
  (already in `ModelManager`) may be sufficient.
- **TensorRT optional**: Should TensorRT EP be a hard dependency in GPU
  containers, or a soft optional that activates if present? Leaning toward
  soft — keeps CPU-only and non-NVIDIA paths clean.
- **Cache invalidation**: When a model version bumps, how do we invalidate
  cached optimised artefacts? Key by model hash + GPU arch?
- **Accuracy validation**: After optimisation (especially INT8), should we run
  a quick sanity check (WER on a few samples) before activating the fast path?
