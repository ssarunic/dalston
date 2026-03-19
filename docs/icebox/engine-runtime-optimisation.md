# Engine Runtime Optimisation

|              |                                                                                      |
| ------------ | ------------------------------------------------------------------------------------ |
| **Idea**     | Background-optimise inference sessions across all engine runtimes, cache to S3        |
| **Priority** | Medium — meaningful latency wins, no blocking deployment changes                     |
| **Status**   | Icebox                                                                               |

## Thesis

Dalston runs four distinct ML runtimes — ONNX Runtime, CTranslate2
(faster-whisper), NeMo/PyTorch, and HuggingFace Transformers — plus PyTorch
for diarisation and alignment. Each loads models with safe defaults and serves
immediately. But every runtime has hardware-specific optimisation capabilities
we don't currently exploit, and the performance gap compounds on
high-throughput deployments.

The core idea: **start serving immediately with generic settings, then
background-optimise for the specific hardware and hot-swap the model session**.
This applies uniformly across runtimes. A shared `ModelOptimiser` protocol in
the base `ModelManager` drives it, with runtime-specific strategies plugged in.

## Problem

Today, each model manager loads models with static configuration:

- **OnnxModelManager** — CUDAExecutionProvider or CPUExecutionProvider, optional
  INT8 via `DALSTON_QUANTIZATION`. No graph fusion, no IO binding, no
  TensorRT backend, no GPU-architecture-specific tuning.
- **FasterWhisperModelManager** — `compute_type` (float16/int8/float32) set at
  init. No flash attention, no batched decoding, no CTranslate2 quantisation
  calibration.
- **NeMoModelManager** — vanilla PyTorch eager mode. No `torch.compile`, no
  CUDA graphs, no flash attention opt-in, no TorchScript export.
- **HFTransformersModelManager** — default pipeline. No BetterTransformer,
  no flash attention 2, no `torch.compile`, no quantisation (bitsandbytes,
  GPTQ, AWQ).
- **Diarisation engines** (pyannote, NeMo MSDD/Sortformer) — same PyTorch
  eager mode as NeMo transcription.
- **Alignment engine** (phoneme-align, wav2vec2) — unoptimised torchaudio CTC
  forced alignment.

Every request on every GPU gets the same inference path regardless of hardware.

## Proposed Approach

### Two-Phase Startup (All Runtimes)

The pattern is runtime-agnostic:

**Phase 1 — Generic (current behaviour, immediate):** Engine starts, loads
model with safe defaults, declares itself ready. Serves traffic within seconds.

**Phase 2 — Background optimisation (new):** While serving traffic, a background
task profiles the hardware, applies runtime-specific optimisations, and
hot-swaps the model session. Persists optimised artefacts to S3.

```python
class OptimisationStatus(str, Enum):
    GENERIC = "generic"       # Phase 1 — default session
    OPTIMISING = "optimising" # Phase 2 — background work in progress
    OPTIMISED = "optimised"   # Phase 2 complete — fast path active

class ModelOptimiser(Protocol):
    """Each model manager implements this to provide runtime-specific optimisation."""
    def can_optimise(self, model_id: str, device: str) -> bool: ...
    def optimise(self, model_id: str, model: T) -> T: ...
    def cache_key(self, model_id: str) -> str: ...
```

The base `ModelManager` orchestrates the lifecycle (background thread, S3
cache check, hot-swap with reference counting). Each manager implements the
runtime-specific `optimise()` strategy.

### Per-Runtime Optimisation Strategies

#### ONNX Runtime (OnnxModelManager)

| Technique | What it does | Expected impact |
|-----------|-------------|-----------------|
| Graph optimisation | `onnxruntime.transformers.optimizer` or `onnxsim` — fuses attention layers, eliminates redundant ops, constant folding | 10–20% latency reduction |
| IO binding | `IOBinding` API — pre-allocates GPU tensors, avoids CPU↔GPU copies | Significant for multi-stage pipelines |
| FusedAttention | Exploit FastConformer's local attention via ONNX RT's `FusedAttention` operator | Model-specific, verify export |
| TensorRT EP | `TensorrtExecutionProvider` as optional backend — build TRT engines in background, swap from CUDA EP | 20–40% on NVIDIA GPUs |
| Calibrated INT8 | INT8 quantisation with calibration dataset (vs current uncalibrated flag) | Better accuracy/speed tradeoff |

#### CTranslate2 / faster-whisper (FasterWhisperModelManager)

| Technique | What it does | Expected impact |
|-----------|-------------|-----------------|
| Flash attention | CTranslate2 supports flash attention on Ampere+ GPUs — currently not explicitly enabled | 15–30% on long sequences |
| INT8 with calibration | CTranslate2's `quantize` tool with calibration data vs static quantisation | Better accuracy at INT8 |
| Compute type auto-select | Auto-detect optimal compute type per GPU arch (float16 on Ampere+, int8_float16 on Turing) | Always-optimal precision |
| CUDA graphs | CTranslate2 supports CUDA graph capture for fixed-size inputs — reduces kernel launch overhead | 5–10% on short audio |

#### NeMo / PyTorch (NeMoModelManager + diarisation + alignment)

| Technique | What it does | Expected impact |
|-----------|-------------|-----------------|
| `torch.compile` | PyTorch 2.x compilation with inductor backend — fuses ops, optimises memory access patterns | 15–40% depending on model |
| CUDA graphs | Capture and replay fixed computation graphs — eliminates kernel launch overhead | 10–20% on repeated inference |
| Flash attention 2 | `torch.nn.functional.scaled_dot_product_attention` with flash backend | 20–30% on long sequences |
| Mixed precision | `torch.autocast` with hardware-appropriate dtype | 10–20% on Ampere+ |
| TorchScript export | Script the model for graph-mode execution — one-time cost, persistent benefit | 10–15% |

Applies equally to:
- Parakeet transcription (FastConformer)
- Pyannote 4.0 diarisation
- NeMo MSDD / Sortformer diarisation
- Wav2Vec2 phoneme alignment

#### HuggingFace Transformers (HFTransformersModelManager)

| Technique | What it does | Expected impact |
|-----------|-------------|-----------------|
| BetterTransformer | `model.to_bettertransformer()` — fused attention kernels, no model change | 10–20% |
| Flash attention 2 | `model = AutoModel.from_pretrained(..., attn_implementation="flash_attention_2")` | 20–30% on long audio |
| `torch.compile` | Same as NeMo — compile the underlying model | 15–30% |
| bitsandbytes INT8/INT4 | Post-training quantisation via `BitsAndBytesConfig` | 2x memory reduction, modest speed gain |
| ONNX export + ORT | Export to ONNX and switch to OnnxModelManager path | Converges with ONNX optimisations above |

#### vLLM (already optimised)

vLLM engines already use PagedAttention, continuous batching, and CUDA graphs
internally. Phase 2 for vLLM is likely limited to:

- Tensor parallelism configuration based on detected GPU count
- Quantisation profiles (AWQ, GPTQ) selected per GPU VRAM
- Speculative decoding configuration

Low priority — vLLM's own optimisation loop handles most of this.

### S3 Artefact Cache

Persist optimised artefacts to S3, keyed by runtime + model + hardware:

```
optimised/{runtime}/{model_id}/{hardware_key}/
    onnx/parakeet-tdt-v3/sm_86/           # ONNX + A10G
    ctranslate2/large-v3/sm_75-int8/      # CTranslate2 + T4 + INT8
    pytorch/parakeet-rnnt-0.6b/sm_80/     # torch.compile cache + A100
    pytorch/pyannote-4.0/sm_89/           # Pyannote + L4
```

On startup, check S3 for a cached artefact matching the detected hardware. If
found, load directly (skip Phase 2). If not, run Phase 2 and upload the result.

For `torch.compile`, this means persisting the inductor cache directory. For
CTranslate2, the quantised model files. For ONNX, the optimised graph (and
optional TRT engine). Each runtime has different artefact shapes, but the S3
cache lifecycle is the same.

### Engine Card Extension

The optimisation status is runtime-agnostic and lives on every engine:

```python
class EngineOptimisationInfo(BaseModel):
    status: OptimisationStatus          # generic | optimising | optimised
    runtime: str                        # onnx | ctranslate2 | pytorch | transformers
    techniques_applied: list[str]       # ["graph_fusion", "tensorrt_ep", ...]
    latency_improvement_pct: float | None  # measured improvement vs generic
    cached: bool                        # whether artefact was loaded from S3
```

The orchestrator uses `status` for routing — preferring optimised engines for
latency-sensitive work, or spreading load to generic engines during warm-up.
The `techniques_applied` list is informational, surfaced in the console and
engine status API.

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

Dalston's multi-runtime approach sidesteps this. Each runtime provides portable
defaults, and we close the performance gap opportunistically through background
optimisation rather than requiring it upfront.

## Implementation Priority

Not all runtimes are equally impactful. Suggested order:

1. **ONNX Runtime** — largest gap between default and optimised, most
   techniques available, broadest model coverage (Parakeet CTC/TDT/RNNT +
   Whisper + arbitrary models)
2. **NeMo / PyTorch** — `torch.compile` alone is a significant win for
   Parakeet RNNT and diarisation engines. Applies to 5 engines (nemo
   transcribe, pyannote, nemo-msdd, nemo-sortformer, phoneme-align)
3. **CTranslate2** — flash attention and auto compute-type selection.
   faster-whisper is our most used engine
4. **HF Transformers** — BetterTransformer and flash attention 2 are low-effort
   wins. Lower priority because HF ASR pipeline is our slowest transcription
   path (RTF 0.1 vs 0.03 for others)

## Where This Fits

This builds on existing work:

- **M36 (Runtime Model Management)** — model lifecycle, TTL eviction, the
  `ModelManager` base class that would gain the `ModelOptimiser` protocol
- **M32 (Engine Variant Structure)** — variant YAML could express optimisation
  profiles (e.g., `precision: int8`, `torch_compile: true`)
- **Engine SDK device detection** — `detect_device()` already handles CUDA
  auto-detection; would be extended with GPU architecture profiling
  (compute capability, VRAM, driver version)

No dependency on specific milestone completion — this is additive to the current
architecture.

## Open Questions

- **Calibration dataset**: What representative audio samples should we use for
  INT8 calibration? A standard benchmark (LibriSpeech test-clean) or
  production-representative data?
- **Hot-swap safety**: How do we atomically swap the model session without
  dropping in-flight requests? Reference counting on the model handle
  (already in `ModelManager`) may be sufficient.
- **torch.compile cold start**: First `torch.compile` call is slow (30–120s).
  Can we pre-compile in a background thread without blocking the generic path?
  The inductor cache persistence to S3 should make this a one-time cost.
- **TensorRT optional**: Should TensorRT EP be a hard dependency in GPU
  containers, or a soft optional that activates if present? Leaning toward
  soft — keeps CPU-only and non-NVIDIA paths clean.
- **Cache invalidation**: When a model version bumps, how do we invalidate
  cached optimised artefacts? Key by model hash + GPU arch + runtime version?
- **Accuracy validation**: After optimisation (especially INT8), should we run
  a quick sanity check (WER on a few samples) before activating the fast path?
- **Non-GPU hardware**: Should this extend to CPU-specific optimisations
  (AVX-512 detection, OpenVINO EP for Intel, CoreML for Apple Silicon)?
  Low priority but architecturally clean if the protocol supports it.
