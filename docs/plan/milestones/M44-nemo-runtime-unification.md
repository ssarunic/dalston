# M44: NeMo Runtime Unification

| | |
|---|---|
| **Goal** | NeMo RT engines use dynamic model loading, consolidating per-model containers into per-engine containers |
| **Duration** | 3-4 days |
| **Dependencies** | M43 (Real-Time Engine Unification) |
| **Deliverable** | Two consolidated RT containers: `stt-rt-nemo` and `stt-rt-onnx` |
| **Status** | Implemented |

## User Story

> *"As a platform operator, I can deploy a single NeMo RT container that serves any Parakeet model variant, with models downloaded on-demand."*

---

## Problem

NeMo/Parakeet RT engines currently use per-model containers:

```
stt-rt-transcribe-parakeet-rnnt-0.6b
stt-rt-transcribe-parakeet-rnnt-1.1b
stt-rt-transcribe-parakeet-onnx-ctc-0.6b
stt-rt-transcribe-parakeet-onnx-tdt-0.6b-v3
...
```

This means:

- Adding a new Parakeet model requires building and deploying a new image
- GPU nodes run multiple containers for different models (wasted VRAM)
- Cannot download new models without rebuilding containers
- Inconsistent with M43's faster-whisper approach

---

## Solution

Consolidate into two engine-ID-based containers with dynamic model loading:

```text
Before: 6+ NeMo RT images (per-model)
After:  2 RT images (stt-rt-nemo, stt-rt-onnx)
```

| Container | Runtime | Models |
|-----------|---------|--------|
| `stt-rt-nemo` | nemo | parakeet-rnnt-0.6b, parakeet-rnnt-1.1b, parakeet-ctc-* |
| `stt-rt-onnx` | onnx | parakeet-onnx-tdt-*, parakeet-onnx-ctc-* |

---

## Phases

### Phase 1: NeMo ModelManager

**Deliverables:**

- [x] `NeMoModelManager` in `dalston/engine_sdk/managers/nemo.py`
- [x] Support for RNNT, CTC, and TDT architectures
- [x] Model download from HuggingFace on first request
- [x] TTL-based eviction (same pattern as `FasterWhisperModelManager`)

**Key challenge:** NeMo models have different architectures requiring different loading code:

```python
# RNNT models
model = nemo_asr.models.EncDecRNNTBPEModel.from_pretrained("nvidia/parakeet-rnnt-0.6b")

# CTC models
model = nemo_asr.models.EncDecCTCModelBPE.from_pretrained("nvidia/parakeet-ctc-0.6b")

# TDT models (ONNX)
model = nemo_asr.models.EncDecRNNTBPEModel.from_pretrained("nvidia/parakeet-tdt-1.1b")
```

**Solution:** Model metadata in registry includes `architecture` field to select loader:

```python
class NeMoModelManager(ModelManager[ASRModel]):
    LOADERS = {
        "rnnt": nemo_asr.models.EncDecRNNTBPEModel,
        "ctc": nemo_asr.models.EncDecCTCModelBPE,
        "tdt": nemo_asr.models.EncDecRNNTBPEModel,  # TDT uses RNNT base
    }

    def _load_model(self, model_id: str) -> ASRModel:
        metadata = self._get_model_metadata(model_id)
        loader = self.LOADERS[metadata.architecture]
        return loader.from_pretrained(model_id, map_location=self._device)
```

### Phase 2: ONNX ModelManager

**Deliverables:**

- [x] `OnnxModelManager` in `dalston/engine_sdk/managers/onnx.py`
- [x] Support for ONNX Runtime inference via onnx-asr library
- [x] Separate from PyTorch NeMo for lighter container

**Key difference:** ONNX models use `onnxruntime` instead of full NeMo:

```python
class OnnxModelManager(ModelManager[OnnxASRModel]):
    def _load_model(self, model_id: str) -> OnnxASRModel:
        # Download model files
        model_path = self._download_or_cache(model_id)

        # Load with ONNX Runtime
        return OnnxASRModel(
            encoder_path=model_path / "encoder.onnx",
            decoder_path=model_path / "decoder.onnx",
            tokenizer_path=model_path / "tokenizer.model",
        )
```

### Phase 3: RT Engine Consolidation

**Deliverables:**

- [x] `engines/stt-rt/parakeet/engine.py` - Consolidated NeMo RT engine (updated to use NeMoModelManager)
- [x] `engines/stt-rt/parakeet-onnx/engine.py` - Consolidated ONNX RT engine (updated to use OnnxModelManager)
- [x] Updated `docker-compose.yml` with new consolidated service definitions (`stt-rt-nemo`, `stt-rt-onnx`)
- [ ] Remove obsolete per-model RT engine directories (optional - kept for backward compatibility)

**Files to delete:**

```
engines/stt-rt/parakeet-rnnt-0.6b/
engines/stt-rt/parakeet-rnnt-1.1b/
engines/stt-rt/parakeet-onnx-ctc-0.6b/
engines/stt-rt/parakeet-onnx-tdt-0.6b-v3/
```

### Phase 4: Model Registry Integration

**Deliverables:**

- [ ] Add NeMo models to model catalog with `architecture` field
- [ ] Model download status tracking in web console
- [ ] Pre-download models via admin API (optional)

---

## API Changes

### Model Catalog Entry

```json
{
  "id": "parakeet-rnnt-1.1b",
  "engine_id": "nemo",
  "loaded_model_id": "nvidia/parakeet-rnnt-1.1b",
  "architecture": "rnnt",
  "size_gb": 1.2,
  "languages": ["en"],
  "capabilities": {
    "streaming": true,
    "word_timestamps": true
  }
}
```

### Worker Heartbeat

Same as M43 - `loaded_models` in heartbeat:

```json
{
  "worker_id": "stt-rt-nemo-1",
  "engine_id": "nemo",
  "loaded_models": ["parakeet-rnnt-1.1b"],
  "active_sessions": 2
}
```

---

## Docker Compose

```yaml
# Consolidated NeMo RT (GPU)
stt-rt-nemo:
  image: dalston/stt-rt-nemo:1.0.0
  build:
    context: .
    dockerfile: engines/stt-rt/nemo/Dockerfile
  environment:
    DALSTON_WORKER_ID: stt-rt-nemo
    DALSTON_MODEL_TTL_SECONDS: 3600
    DALSTON_MAX_LOADED_MODELS: 2
    DALSTON_MODEL_PRELOAD: parakeet-rnnt-1.1b  # Optional
  deploy:
    resources:
      reservations:
        devices:
          - driver: nvidia
            count: 1
            capabilities: [gpu]

# Consolidated NeMo ONNX RT (CPU-friendly)
stt-rt-onnx:
  image: dalston/stt-rt-onnx:1.0.0
  build:
    context: .
    dockerfile: engines/stt-rt/onnx/Dockerfile
  environment:
    DALSTON_WORKER_ID: stt-rt-onnx
    DALSTON_MODEL_TTL_SECONDS: 3600
    DALSTON_MAX_LOADED_MODELS: 1
    DALSTON_MODEL_PRELOAD: parakeet-onnx-tdt-0.6b-v3
```

---

## Success Criteria

- [x] NeMo RT engines serve any model variant without image rebuild
- [x] Models downloaded on-demand from HuggingFace
- [x] Number of NeMo RT images reduced from N to 2 (nemo, onnx)
- [x] Session Router routes to warm workers when available (via `loaded_models` in heartbeat)
- [ ] Cold-start latency < 90s for largest model (1.1B) (requires testing)

---

## Migration Plan

1. Deploy new `stt-rt-nemo` and `stt-rt-onnx` containers alongside existing
2. Verify both can serve all model variants
3. Update Session Router to prefer new consolidated workers
4. Deprecate old per-model containers
5. Remove old container definitions from docker-compose.yml

---

## References

- [M43: Real-Time Engine Unification](M43-realtime-engine-unification.md) - Pattern for faster-whisper
- [M36: Runtime Model Management](M36-runtime-model-management.md) - Batch `ModelManager` implementation
- [NeMo ASR Documentation](https://docs.nvidia.com/deeplearning/nemo/user-guide/docs/en/stable/asr/intro.html)
