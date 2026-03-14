# GPU Memory Stability for ASR Inference: A Practical Framework

## Overview

GPU memory behaviour during ASR inference is fundamentally different from classical software. The same model on the same machine can OOM on one audio file and succeed on another, depending on duration, speech density, batch composition, and current memory fragmentation state. The right mental model is not "prevent all crashes" but **resilience by design**: a system that negotiates difficult inputs gracefully rather than falling over.

---

## Why ASR Inference Is Uniquely Volatile

GPU memory during ASR has three variable components stacked on top of each other:

### Static Weight Footprint

Model parameters loaded once. Parakeet TDT 0.6B is roughly 1.2 GB in FP16, ~600 MB in INT8. Predictable and boring — not where problems come from.

### Activation Memory

Intermediate tensors created during the forward pass. Modern FastConformer architectures (what Parakeet uses) employ **local attention** rather than full self-attention, so you don't get the classic O(n²) blowup with sequence length. But activations still grow linearly with input duration, and that linear growth across a deep encoder stack adds up fast. A 30-second chunk and a 5-minute file produce meaningfully different memory profiles, even with local attention. The fix is the same regardless: **bound your inputs**.

### Framework Overhead and Fragmentation

PyTorch's caching allocator reserves memory aggressively and doesn't return it to the OS. When applications repeatedly allocate and free small memory blocks, GPU memory can become fragmented into many small unusable regions, leading to OOM errors even when sufficient total free memory exists. This is the "phantom OOM" — `nvidia-smi` shows free space, but PyTorch can't find a contiguous block large enough for the next allocation. It's the most confusing failure mode for teams new to GPU infrastructure, because the numbers seem to lie.

### The Pipeline Compounding Effect

For ASR specifically, the problem compounds because a transcription job isn't one model — in Dalston's pipeline you're running transcription, then alignment (wav2vec2), then diarisation (pyannote/MSDD), sequentially on the same GPU.

**Critical gotcha**: the alignment stage is often hungrier than transcription itself. wav2vec2 operates at a higher effective sampling rate with dense feature extraction, so per-second-of-audio it can consume more VRAM than Parakeet. If your pipeline OOMs, check the alignment or diarisation handover first — that's where memory pressure peaks.

---

## The Stability Framework: Five Layers of Defence

No single technique solves everything. You need all five layers working together.

### Layer 1: Bound the Input — Audio Chunking

This is the **single most important technique**. It converts an unpredictable memory problem into a predictable one.

**Principle**: never feed raw, unbounded audio into a model. Chunk it into fixed-length segments with overlapping strides, run inference on each chunk, and merge the outputs. The CTC architecture can be exploited to achieve robust speech recognition on arbitrarily long files by doing inference on overlapping chunks, dropping the inferred logits on the sides, and chaining the remaining logits to recover results extremely close to what the model would have predicted on the full audio.

**For Whisper-family models** (via faster-whisper): VAD-first chunking — run Silero VAD to detect speech segments, then batch those segments. VAD-based segment transcription reduces WER and enables accurate batched inference.

**For Conformer/Parakeet models**: NeMo provides chunked inference that splits audio into configurable segments, performing inference on each individually and concatenating results.

For Dalston, the pipeline pattern is:

```
audio_in → VAD segmentation → chunk to max_duration (e.g. 30s)
         → batch chunks (batch_size tuned per model/GPU)
         → transcribe → merge outputs
```

The two knobs are `max_duration` and `batch_size`. Automating how they're set is what Layer 3 addresses.

### Layer 2: Control the Memory Allocator

PyTorch's caching allocator is both your friend and your enemy. It caches freed memory for fast reuse, but this creates fragmentation over time.

Key settings for inference stability, set as an environment variable in engine containers:

```bash
PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True,max_split_size_mb:512,garbage_collection_threshold:0.8
```

What each does:

- **`expandable_segments:True`** — Instructs the allocator to create CUDA allocations that can later be expanded, better handling cases where allocation sizes change frequently such as changing batch sizes. This is the single most impactful setting for variable-length inference workloads.
- **`max_split_size_mb:512`** — Prevents the allocator from splitting large blocks, reducing fragmentation at the cost of some memory waste.
- **`garbage_collection_threshold:0.8`** — Proactively reclaims memory when usage exceeds 80%, rather than waiting for an allocation failure.

Between pipeline stages, explicitly release models and clear the cache. **Ordering matters** — Python's reference counting can be stubborn with circular references in complex pipelines, so `gc.collect()` must come before the CUDA cache clear:

```python
import gc
import torch

del model
gc.collect()                    # break circular refs first
torch.cuda.empty_cache()        # then release CUDA memory
```

This is especially critical at the transcription → alignment handover, where you're swapping a Conformer encoder for a wav2vec2 model on the same GPU.

### Layer 3: Profile, Don't Guess — Memory-Aware Configuration

This is where most people get stuck in the "tedious tweaking" loop. They guess at batch sizes through trial and error. The better approach is systematic profiling.

**Triton Model Analyzer** is the gold standard tool. It uses Performance Analyzer to send requests to your model while measuring GPU memory and compute utilisation — specifically useful for characterising GPU memory requirements under different batching and model instance configurations.

Even without Triton, you can build a profiling harness that binary-searches for the maximum stable batch size:

```python
def find_safe_batch_size(model, gpu_id, audio_duration_s=30.0, safety_margin=0.75):
    """Binary search for max stable batch size.

    Start conservative (75% margin pre-warm-up). After warm-up
    tames the allocator, the runtime adapter can push toward 85%.
    """
    total_mem = torch.cuda.get_device_properties(gpu_id).total_mem
    target_mem = total_mem * safety_margin

    low, high = 1, 64
    safe_batch = 1

    while low <= high:
        mid = (low + high) // 2
        torch.cuda.reset_peak_memory_stats()

        try:
            # Use textured audio (white noise), not zeros —
            # some encoders follow different memory paths with
            # silence vs. actual signal
            dummy_batch = create_noise_batch(mid, duration_s=audio_duration_s)
            with torch.no_grad():
                _ = model.transcribe(dummy_batch)

            peak = torch.cuda.max_memory_allocated()
            if peak < target_mem:
                safe_batch = mid
                low = mid + 1
            else:
                high = mid - 1
        except RuntimeError:  # OOM
            high = mid - 1
        finally:
            gc.collect()
            torch.cuda.empty_cache()

    return safe_batch
```

**Key**: profile at worst-case input, not average. Use the maximum audio duration you'll accept, with a safety margin of **75% during initial calibration** (before the allocator has settled). The runtime adaptive layer can later push this toward **85%** once warm-up has stabilised memory patterns.

For **ONNX Runtime** (the Parakeet deployment path), the equivalent lever is `max_workspace_size_bytes`. This parameter controls the maximum GPU memory the model can use temporarily during execution, defaulting to 1 GB. Set it explicitly based on your profiling rather than relying on the default.

### Layer 4: Warm-Up and Pre-Allocation

Cold-start OOM is a distinct problem from steady-state OOM. The first inference after model load often allocates more memory than subsequent ones — frameworks lazily initialise CUDA kernels, cuDNN selects algorithms, and the caching allocator hasn't yet settled into a stable pattern.

**Model warm-up** means running representative inference before accepting real traffic. Triton exposes the ability to run ModelWarmup requests when first loading a model to ensure it's sufficiently warmed up before being marked "READY" for inference.

Two details matter for warm-up quality:

1. **Warm up at max batch size** — if you only warm up with `batch_size=1`, the allocator's internal state won't reflect production workloads and you'll get fragmentation on the first real batch.
2. **Use realistic synthetic data** — generate white noise or actual speech-like audio, not tensors of zeros. Some encoders (particularly those with normalisation layers or VAD preprocessing) follow different computation and memory paths with silence versus textured audio. You want the warm-up to exercise the same code paths that production traffic will hit.

For Dalston, this means the engine's `/health` endpoint shouldn't return healthy until:

1. The model is loaded
2. A warm-up inference has completed at the calibrated max batch size
3. Peak memory has been recorded and stored as engine metadata

After warm-up, the allocator is in a "tamed" state — subsequent inferences reuse the same allocation patterns. This is when you can safely raise the safety margin from 75% to 85%.

### Layer 5: Runtime Guardrails and Monitoring

Even with all the above, you need runtime protection.

**Memory-aware request admission** — before accepting a job, check headroom:

```python
estimated_peak = model_base_memory + (batch_size * per_sample_cost * duration_factor)
```

If `estimated_peak > available_memory * safety_factor`, reduce the batch size for this request or queue it.

**Monitoring** — the metric to watch is **`dcgm_fb_free`** (framebuffer free) — it's the most honest indicator of available runway before a crash. Memory usage climbing steadily over minutes is the pre-OOM warning. Set alerts at:

- **85%**: trigger batch size reduction
- **95%**: reject new requests until memory stabilises

**Process isolation** — each engine in its own container with GPU memory limits. If an engine OOMs, it takes down only that container. The gateway routes to a different instance while the crashed one restarts. This is where the runtime-based container architecture pays off.

---

## Making Parameters Self-Tuning

### Step 1: Calibration on First Model Load

When an engine loads a new model variant (via `dalston pull`), run the binary-search profiler. Store results in the model registry:

```yaml
parakeet-tdt-0.6b:
  gpu: g5.xlarge          # A10G, 24GB
  compute_type: float16
  max_batch_size: 16      # calibrated at 75% margin
  max_audio_duration_s: 30
  peak_memory_gb: 18.2
  warm_up_completed: true
  post_warmup_margin: 0.85
```

This data feeds directly into the model library at dalston.ai/models — each model page shows not just WER benchmarks but VRAM requirements, calibrated batch sizes, and RTF on reference hardware.

### Step 2: Runtime Adaptive Batch Sizing

Start at the calibrated batch size. Monitor peak memory per request. If you consistently see headroom, nudge up. If you see pressure, back off:

```python
class AdaptiveBatcher:
    def __init__(self, calibrated_batch_size, min_batch=1, max_batch=64):
        self.current_batch = calibrated_batch_size
        self.history = deque(maxlen=100)

    def observe(self, peak_memory_fraction):
        self.history.append(peak_memory_fraction)
        if len(self.history) < 20:
            return  # not enough data yet

        avg = sum(self.history) / len(self.history)

        if avg < 0.70:
            self.current_batch = min(self.current_batch + 1, self.max_batch)
        elif avg > 0.90:
            # back off aggressively — OOM is expensive
            self.current_batch = max(self.current_batch - 2, self.min_batch)
```

### Step 3: OOM Recovery Protocol

When an OOM occurs (and it will, because real audio is messier than synthetic calibration data):

1. Catch `torch.cuda.OutOfMemoryError`
2. `gc.collect()` then `torch.cuda.empty_cache()`
3. Retry with halved batch size
4. If still OOM, re-chunk the audio into smaller segments
5. Log the failure with full context (audio duration, batch size, peak memory) for offline calibration refinement
6. Report adjusted parameters back to the model registry

This creates a feedback loop: the system starts with calibrated defaults, observes real-world behaviour, and converges toward stable parameters for each model-GPU pair. Over time, OOMs become rare rather than absent — and when they do occur, the system recovers automatically rather than crashing.

---

## Design Philosophy

In classical software, a crash is a bug. In ASR inference, an occasional OOM is a normal operating condition that the system negotiates gracefully. Dalston doesn't promise that models never hit memory limits — it promises that when they do, the system backs off, retries, learns, and converges. **Resilient by design**.
