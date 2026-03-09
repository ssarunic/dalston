# M41: New Engine Types

| | |
|---|---|
| **Goal** | Support ONNX Parakeet, HuggingFace Transformers ASR, and vLLM audio models |
| **Duration** | 5-7 days |
| **Dependencies** | M39 (Model Cache & TTL), M40 (Model Registry) |
| **Deliverable** | Three new engine containers: parakeet-onnx, hf-asr, vllm-asr |
| **Status** | Planned |

## Overview

Add three new engine types to expand Dalston's model coverage:

1. **Parakeet ONNX**: Lightweight NVIDIA Parakeet using ONNX Runtime instead of NeMo (~5GB → ~1GB image)
2. **HF-ASR**: Generic HuggingFace Transformers ASR for community fine-tunes (Wav2Vec2, HuBERT, MMS, Whisper)
3. **vLLM-ASR**: Audio-capable LLMs via vLLM (Voxtral, Qwen2-Audio, future audio LLMs)

### Why These Engines

| Engine | Use Case | Models |
|--------|----------|--------|
| parakeet-onnx | Fast Parakeet inference without NeMo overhead | Parakeet TDT/CTC v2, v3 |
| hf-asr | Community fine-tunes, multilingual (MMS), research models | 10,000+ HF ASR models |
| vllm-asr | State-of-the-art accuracy (lowest WER) | Voxtral, Qwen2-Audio |

---

## 41.1: Parakeet ONNX Engine

Replace the heavy NeMo toolkit (~5GB container) with lightweight ONNX Runtime (~1GB).

### Current State

- `engines/stt-transcribe/parakeet/` uses NeMo toolkit
- NeMo pulls PyTorch, Megatron, CUDA runtime
- Container is ~5GB, slow cold start

### Target State

- New `engines/stt-transcribe/parakeet-onnx/`
- Uses ONNX Runtime with TensorRT provider
- Container ~1GB, fast cold start
- Same accuracy, ~2x faster inference

### Directory Structure

```
engines/stt-transcribe/parakeet-onnx/
├── Dockerfile
├── engine.py
├── engine.yaml
├── requirements.txt
└── README.md
```

### Dockerfile

*Follow pattern from existing engine Dockerfiles (e.g., `engines/stt-transcribe/faster-whisper/Dockerfile`)*

### Key Dependencies

- onnxruntime-gpu
- soundfile, librosa
- sentencepiece
- huggingface_hub

### engine.yaml

```yaml
id: parakeet-onnx
name: Parakeet ONNX Runtime
stage: transcribe
capabilities:
  languages: [en]  # TDT v3 adds: es, de, fr, it, pt, nl, pl, cs, ru, uk, hi, zh, ja, ko
  word_timestamps: true
  punctuation: false
  streaming: false
models:
  - id: parakeet-tdt-1.1b-onnx
    hf_model_id: nvidia/parakeet-tdt-1.1b-onnx
    default: true
  - id: parakeet-ctc-0.6b-onnx
    hf_model_id: nvidia/parakeet-ctc-0.6b-onnx
```

### Engine Interface

```python
class ParakeetOnnxModelManager(ModelManager[ort.InferenceSession]):
    """Model manager for Parakeet ONNX models."""
    def _load_model(self, model_id: str) -> ort.InferenceSession: ...
    def _unload_model(self, model: ort.InferenceSession) -> None: ...

class ParakeetOnnxEngine(Engine):
    """Parakeet transcription using ONNX Runtime."""
    SAMPLE_RATE = 16000
    MAX_AUDIO_SECONDS = 30

    def process(self, input: TaskInput) -> TaskOutput: ...
    def _process_single(self, session: ort.InferenceSession, audio: np.ndarray) -> list[Segment]: ...
    def _process_chunked(self, session: ort.InferenceSession, audio: np.ndarray) -> list[Segment]: ...
    def _decode_tdt_outputs(self, outputs: list[np.ndarray], duration: float) -> list[Segment]: ...
    def _merge_overlapping(self, segments: list[Segment]) -> list[Segment]: ...
```

*Follow existing engine pattern (e.g., `engines/stt-transcribe/faster-whisper/engine.py`). Use `EngineRunner` for the `__main__` entry point.*

### docker-compose.yml

*Add service following the pattern of existing transcribe engines in `docker-compose.yml`. Engine ID: `parakeet-onnx`.*

---

## 41.2: HuggingFace Transformers ASR Engine

Generic engine for any HuggingFace model with `pipeline_tag: automatic-speech-recognition`.

### Directory Structure

```
engines/stt-transcribe/hf-asr/
├── Dockerfile
├── engine.py
├── engine.yaml
├── requirements.txt
├── output_handlers/
│   ├── __init__.py
│   ├── whisper.py
│   ├── wav2vec2.py
│   └── mms.py
└── README.md
```

### Dockerfile

*Follow pattern from existing engine Dockerfiles. Use `nvidia/cuda:12.4.0-runtime-ubuntu24.04` as base image since HF models typically need GPU.*

### Key Dependencies

- torch, torchaudio
- transformers, accelerate
- soundfile, librosa

### engine.yaml

```yaml
id: hf-asr
name: HuggingFace Transformers ASR
stage: transcribe
capabilities:
  languages: null  # Multilingual (model-dependent)
  word_timestamps: true
  punctuation: true  # Model-dependent
  streaming: false
models: dynamic  # Any HF model with pipeline_tag=automatic-speech-recognition
```

### Engine Interface

```python
class HFTransformersModelManager(ModelManager):
    """Model manager for HuggingFace Transformers ASR models."""
    def _load_model(self, model_id: str): ...
    def _unload_model(self, model) -> None: ...

class HFASREngine(Engine):
    """Generic HuggingFace ASR pipeline engine."""
    def process(self, input: TaskInput) -> TaskOutput: ...
    def _normalize_output(self, result: dict[str, Any], model_id: str, language: str | None) -> TranscribeOutput: ...
```

*Key design decision: Use `transformers.pipeline("automatic-speech-recognition")` for model loading. Output normalization must handle different formats across architectures (Whisper returns chunks with timestamps, Wav2Vec2/MMS return text only). Follow existing engine pattern for `__main__` entry point.*

### docker-compose.yml

*Add service following the pattern of existing transcribe engines. Engine ID: `hf-asr`. Include GPU profile and `nvidia` device reservation.*

---

## 41.3: vLLM Audio ASR Engine

Audio-capable LLMs via vLLM for state-of-the-art accuracy.

### Directory Structure

```
engines/stt-transcribe/vllm-asr/
├── Dockerfile
├── engine.py
├── engine.yaml
├── requirements.txt
├── adapters/
│   ├── __init__.py
│   ├── base.py
│   ├── voxtral.py
│   └── qwen2_audio.py
└── README.md
```

### Dockerfile

*Use `vllm/vllm-openai:v0.6.0` as base image. Follow pattern from existing engine Dockerfiles.*

### Key Dependencies

- vllm[audio]
- mistral-common[audio] (for Voxtral prompt building)
- soundfile, librosa

### engine.yaml

```yaml
id: vllm-asr
name: vLLM Audio ASR
stage: transcribe
capabilities:
  languages: null  # Multilingual
  word_timestamps: false  # Audio LLMs don't produce timestamps
  punctuation: true
  streaming: false
models:
  - id: voxtral-mini-3b
    hf_model_id: mistralai/Voxtral-Mini-3B-2507
    adapter: voxtral
    default: true
  - id: voxtral-small-24b
    hf_model_id: mistralai/Voxtral-Small-24B-2507
    adapter: voxtral
  - id: qwen2-audio-7b
    hf_model_id: Qwen/Qwen2-Audio-7B
    adapter: qwen2_audio
```

### Engine Interface

```python
class AudioLLMAdapter(ABC):
    """Base class for model-specific prompt building and output parsing."""
    @abstractmethod
    def build_prompt(self, audio_path: Path, language: str | None = None) -> Any: ...
    @abstractmethod
    def parse_output(self, raw_text: str, language: str | None = None) -> TranscribeOutput: ...

class VLLMModelManager(ModelManager[LLM]):
    """Model manager for vLLM audio models."""
    def _load_model(self, model_id: str) -> LLM: ...
    def _unload_model(self, model: LLM) -> None: ...

class VLLMASREngine(Engine):
    """vLLM-based ASR for audio LLMs."""
    def process(self, input: TaskInput) -> TaskOutput: ...
    def _get_adapter(self, model_id: str) -> AudioLLMAdapter: ...
```

*Key design decisions:*
- *Adapter pattern isolates model-specific prompt building and output parsing (Voxtral uses `mistral-common`, Qwen2 uses its own format)*
- *Use `SamplingParams(temperature=0.0)` for deterministic transcription*
- *`max_loaded=1` by default since these models are large*
- *`shm_size: '8gb'` required in docker-compose for vLLM shared memory*

### docker-compose.yml

*Add service following the pattern of existing transcribe engines. Engine ID: `vllm-asr`. Include GPU profile, `nvidia` device reservation, and `shm_size: '8gb'`.*

---

## Important: No Word Timestamps from Audio LLMs

Audio LLMs (Voxtral, Qwen2-Audio) produce **text only** - no timing information.

If the job requires word timestamps (for subtitles, diarization alignment, etc.), the orchestrator must chain the **alignment stage** after vLLM-ASR:

```
vllm-asr (transcribe) → phoneme-align (add timestamps)
```

This is handled automatically by the orchestrator when:

- User requests `timestamps_granularity: word`
- The transcription engine declares `word_timestamps: false`

---

## Catalog Registration

Add all three engines to `generated_catalog.json` or database seeding with their `id`, `image`, `stage`, and `runtime` fields.

---

## Verification Checklist

- [ ] Each engine builds successfully as a Docker container
- [ ] Parakeet ONNX container is ~1GB (vs ~5GB for NeMo version) with equivalent accuracy
- [ ] HF-ASR correctly normalizes output across architectures (Whisper, Wav2Vec2, MMS)
- [ ] vLLM-ASR adapters produce correct prompts for Voxtral and Qwen2-Audio
- [ ] Orchestrator chains alignment stage after vLLM-ASR when timestamps requested

---

## Checkpoint

- [ ] **41.1**: Parakeet ONNX engine working, smaller than NeMo, same accuracy
- [ ] **41.2**: HF-ASR engine loads and normalizes output for Whisper, Wav2Vec2, MMS
- [ ] **41.3**: vLLM-ASR engine loads Voxtral, adapters work, alignment chaining works
- [ ] All engines use TTL-based ModelManager
- [ ] Catalog updated with new engines
