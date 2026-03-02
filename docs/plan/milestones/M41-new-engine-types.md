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

```dockerfile
# NVIDIA Parakeet ONNX Runtime Engine
FROM python:3.11-slim

# System dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

# Install dalston engine-sdk
WORKDIR /opt/dalston
COPY pyproject.toml .
COPY dalston/ dalston/
RUN pip install --no-cache-dir -e ".[engine-sdk]"

# Install engine dependencies
WORKDIR /engine
COPY engines/stt-transcribe/parakeet-onnx/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy engine code
COPY engines/stt-transcribe/parakeet-onnx/engine.py .
COPY engines/stt-transcribe/parakeet-onnx/engine.yaml /etc/dalston/engine.yaml

# Environment
ENV DALSTON_MODEL_DIR=/models
ENV HF_HUB_CACHE=/models/huggingface
ENV DALSTON_ENGINE_ID=parakeet-onnx

CMD ["python", "engine.py"]
```

### requirements.txt

```
onnxruntime-gpu>=1.17.0
numpy>=1.24.0
soundfile>=0.12.0
librosa>=0.10.0
sentencepiece>=0.2.0
huggingface_hub>=0.20.0
```

### engine.yaml

```yaml
id: parakeet-onnx
name: Parakeet ONNX Runtime
version: 1.0.0
stage: transcribe
runtime: parakeet-onnx

description: |
  NVIDIA Parakeet transcription using ONNX Runtime.
  Lightweight alternative to NeMo with TensorRT acceleration.

capabilities:
  languages:
    - en  # TDT v2
    # TDT v3 adds: es, de, fr, it, pt, nl, pl, cs, ru, uk, hi, zh, ja, ko
  word_timestamps: true
  punctuation: false
  streaming: false
  max_audio_duration_s: 3600

hardware:
  gpu_required: false
  gpu_optional: true
  min_vram_gb: 2
  min_ram_gb: 4
  supports_cpu: true

performance:
  rtf_gpu: 0.03
  rtf_cpu: 0.5

models:
  - id: parakeet-tdt-1.1b-onnx
    hf_model_id: nvidia/parakeet-tdt-1.1b-onnx
    default: true
  - id: parakeet-ctc-0.6b-onnx
    hf_model_id: nvidia/parakeet-ctc-0.6b-onnx
```

### Engine Implementation

```python
"""NVIDIA Parakeet ONNX Runtime transcription engine."""
from __future__ import annotations
import os
from pathlib import Path

import numpy as np
import onnxruntime as ort
import soundfile as sf
import structlog

from dalston.engine_sdk import Engine, TaskInput, TaskOutput
from dalston.engine_sdk.model_manager import ModelManager
from dalston.common.pipeline_types import (
    TranscribeOutput, Segment, Word, AlignmentMethod, TimestampGranularity
)

logger = structlog.get_logger()


class ParakeetOnnxModelManager(ModelManager[ort.InferenceSession]):
    """Model manager for Parakeet ONNX models."""

    # Mapping from Dalston model ID to HuggingFace model ID
    MODEL_MAP = {
        "parakeet-tdt-1.1b-onnx": "nvidia/parakeet-tdt-1.1b-onnx",
        "parakeet-ctc-0.6b-onnx": "nvidia/parakeet-ctc-0.6b-onnx",
    }

    def __init__(self, device: str = "cuda", **kwargs):
        self.device = device
        super().__init__(**kwargs)

    def _load_model(self, model_id: str) -> ort.InferenceSession:
        hf_model_id = self.MODEL_MAP.get(model_id, model_id)

        # Download model if needed
        from huggingface_hub import snapshot_download
        model_path = snapshot_download(
            hf_model_id,
            cache_dir=os.environ.get("HF_HUB_CACHE", "/models/huggingface"),
        )

        # Find ONNX file
        onnx_path = Path(model_path) / "model.onnx"
        if not onnx_path.exists():
            # Try finding any .onnx file
            onnx_files = list(Path(model_path).glob("*.onnx"))
            if onnx_files:
                onnx_path = onnx_files[0]
            else:
                raise FileNotFoundError(f"No ONNX model found in {model_path}")

        # Create session with appropriate providers
        providers = []
        if self.device == "cuda":
            providers.append(("CUDAExecutionProvider", {"device_id": 0}))
        providers.append("CPUExecutionProvider")

        session = ort.InferenceSession(
            str(onnx_path),
            providers=providers,
        )

        logger.info(
            "onnx_model_loaded",
            model_id=model_id,
            providers=[p if isinstance(p, str) else p[0] for p in providers],
        )
        return session

    def _unload_model(self, model: ort.InferenceSession) -> None:
        del model


class ParakeetOnnxEngine(Engine):
    """Parakeet transcription using ONNX Runtime."""

    SAMPLE_RATE = 16000
    MAX_AUDIO_SECONDS = 30  # ONNX models have chunk limits

    def __init__(self):
        super().__init__()
        device = "cuda" if self._has_cuda() else "cpu"

        self._manager = ParakeetOnnxModelManager(
            device=device,
            ttl_seconds=int(os.environ.get("DALSTON_MODEL_TTL_SECONDS", 3600)),
            max_loaded=int(os.environ.get("DALSTON_MAX_LOADED_MODELS", 2)),
            preload=os.environ.get("DALSTON_MODEL_PRELOAD"),
        )

    def _has_cuda(self) -> bool:
        try:
            import torch
            return torch.cuda.is_available()
        except ImportError:
            return "CUDAExecutionProvider" in ort.get_available_providers()

    def process(self, input: TaskInput) -> TaskOutput:
        model_id = input.config.get(
            "runtime_model_id",
            os.environ.get("DALSTON_DEFAULT_MODEL_ID", "parakeet-tdt-1.1b-onnx")
        )

        session = self._manager.acquire(model_id)
        try:
            # Load audio
            audio, sr = sf.read(str(input.audio_path))
            if sr != self.SAMPLE_RATE:
                import librosa
                audio = librosa.resample(audio, orig_sr=sr, target_sr=self.SAMPLE_RATE)

            # Process in chunks if too long
            if len(audio) / self.SAMPLE_RATE > self.MAX_AUDIO_SECONDS:
                segments = self._process_chunked(session, audio)
            else:
                segments = self._process_single(session, audio)

            # Build output
            full_text = " ".join(s.text for s in segments)

            return TaskOutput(data=TranscribeOutput(
                text=full_text,
                segments=segments,
                language=input.config.get("language", "en"),
                engine_id="parakeet-onnx",
                timestamp_granularity_actual=TimestampGranularity.WORD,
                alignment_method=AlignmentMethod.TDT,
            ))

        finally:
            self._manager.release(model_id)

    def _process_single(
        self,
        session: ort.InferenceSession,
        audio: np.ndarray,
    ) -> list[Segment]:
        """Process a single audio chunk."""
        # Prepare input (model-specific preprocessing)
        audio = audio.astype(np.float32)
        audio_len = np.array([len(audio)], dtype=np.int32)

        # Run inference
        inputs = {
            "audio_signal": audio.reshape(1, -1),
            "length": audio_len,
        }
        outputs = session.run(None, inputs)

        # Decode outputs (model-specific)
        # TDT models return: logits, timestamps
        return self._decode_tdt_outputs(outputs, len(audio) / self.SAMPLE_RATE)

    def _process_chunked(
        self,
        session: ort.InferenceSession,
        audio: np.ndarray,
    ) -> list[Segment]:
        """Process long audio in chunks with overlap."""
        chunk_samples = int(self.MAX_AUDIO_SECONDS * self.SAMPLE_RATE)
        overlap_samples = int(2 * self.SAMPLE_RATE)  # 2 second overlap

        segments = []
        offset = 0

        while offset < len(audio):
            chunk = audio[offset:offset + chunk_samples]
            chunk_segments = self._process_single(session, chunk)

            # Adjust timestamps for offset
            time_offset = offset / self.SAMPLE_RATE
            for seg in chunk_segments:
                seg.start += time_offset
                seg.end += time_offset
                if seg.words:
                    for word in seg.words:
                        word.start += time_offset
                        word.end += time_offset

            segments.extend(chunk_segments)
            offset += chunk_samples - overlap_samples

        # Deduplicate overlapping segments
        return self._merge_overlapping(segments)

    def _decode_tdt_outputs(
        self,
        outputs: list[np.ndarray],
        duration: float,
    ) -> list[Segment]:
        """Decode TDT model outputs to segments with word timestamps."""
        # This is model-specific - TDT outputs token-level predictions
        # Simplified implementation
        logits = outputs[0]  # [1, T, vocab]
        # ... decode and build segments
        return []

    def _merge_overlapping(self, segments: list[Segment]) -> list[Segment]:
        """Merge overlapping segments from chunked processing."""
        # ... merge logic
        return segments


if __name__ == "__main__":
    from dalston.engine_sdk.runner import EngineRunner
    engine = ParakeetOnnxEngine()
    runner = EngineRunner(engine)
    runner.run()
```

### docker-compose.yml Addition

```yaml
stt-batch-transcribe-parakeet-onnx:
  image: dalston/engine-parakeet-onnx:latest
  build:
    context: .
    dockerfile: engines/stt-transcribe/parakeet-onnx/Dockerfile
  volumes:
    - model-cache:/models
  environment:
    <<: *common-env
    DALSTON_ENGINE_ID: parakeet-onnx
    DALSTON_DEFAULT_MODEL_ID: parakeet-tdt-1.1b-onnx
  depends_on:
    - redis
    - minio
```

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

```dockerfile
# HuggingFace Transformers ASR Engine
FROM nvidia/cuda:12.4.0-runtime-ubuntu24.04

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-pip python3-dev \
    ffmpeg libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

# Install dalston engine-sdk
WORKDIR /opt/dalston
COPY pyproject.toml .
COPY dalston/ dalston/
RUN pip3 install --no-cache-dir -e ".[engine-sdk]"

# Install engine dependencies
WORKDIR /engine
COPY engines/stt-transcribe/hf-asr/requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt

# Copy engine code
COPY engines/stt-transcribe/hf-asr/ .
COPY engines/stt-transcribe/hf-asr/engine.yaml /etc/dalston/engine.yaml

# Environment
ENV DALSTON_MODEL_DIR=/models
ENV HF_HUB_CACHE=/models/huggingface
ENV DALSTON_ENGINE_ID=hf-asr

CMD ["python3", "engine.py"]
```

### requirements.txt

```
torch>=2.2.0
torchaudio>=2.2.0
transformers>=4.37.0
accelerate>=0.26.0
soundfile>=0.12.0
librosa>=0.10.0
```

### engine.yaml

```yaml
id: hf-asr
name: HuggingFace Transformers ASR
version: 1.0.0
stage: transcribe
runtime: hf-asr

description: |
  Generic ASR engine for HuggingFace transformers models.
  Supports Whisper, Wav2Vec2, HuBERT, MMS, and other ASR architectures.
  Use for community fine-tunes and research models.

capabilities:
  languages: null  # Multilingual (model-dependent)
  word_timestamps: true
  punctuation: true  # Model-dependent
  streaming: false
  max_audio_duration_s: 3600

hardware:
  gpu_required: false
  gpu_optional: true
  min_vram_gb: 4
  min_ram_gb: 8
  supports_cpu: true

performance:
  rtf_gpu: 0.1  # Model-dependent
  rtf_cpu: 1.0

# Dynamic models - any HF model with pipeline_tag=automatic-speech-recognition
models: dynamic
```

### Engine Implementation

```python
"""HuggingFace Transformers ASR engine."""
from __future__ import annotations
import os
from pathlib import Path
from typing import Any

import torch
from transformers import pipeline, AutoProcessor
import structlog

from dalston.engine_sdk import Engine, TaskInput, TaskOutput
from dalston.engine_sdk.model_manager import ModelManager
from dalston.common.pipeline_types import (
    TranscribeOutput, Segment, Word, AlignmentMethod, TimestampGranularity
)

logger = structlog.get_logger()


class HFTransformersModelManager(ModelManager):
    """Model manager for HuggingFace Transformers ASR models."""

    def __init__(self, device: str = "cuda", torch_dtype=None, **kwargs):
        self.device = device
        self.torch_dtype = torch_dtype or (
            torch.float16 if device == "cuda" else torch.float32
        )
        super().__init__(**kwargs)

    def _load_model(self, model_id: str):
        """Load HuggingFace ASR pipeline."""
        pipe = pipeline(
            "automatic-speech-recognition",
            model=model_id,
            device=self.device if self.device != "cpu" else -1,
            torch_dtype=self.torch_dtype,
        )
        logger.info("hf_model_loaded", model_id=model_id, device=self.device)
        return pipe

    def _unload_model(self, model) -> None:
        del model


class HFASREngine(Engine):
    """Generic HuggingFace ASR pipeline engine."""

    def __init__(self):
        super().__init__()
        device = "cuda" if torch.cuda.is_available() else "cpu"
        torch_dtype = torch.float16 if device == "cuda" else torch.float32

        self._manager = HFTransformersModelManager(
            device=device,
            torch_dtype=torch_dtype,
            ttl_seconds=int(os.environ.get("DALSTON_MODEL_TTL_SECONDS", 3600)),
            max_loaded=int(os.environ.get("DALSTON_MAX_LOADED_MODELS", 2)),
            preload=os.environ.get("DALSTON_MODEL_PRELOAD"),
        )

    def process(self, input: TaskInput) -> TaskOutput:
        model_id = input.config.get(
            "runtime_model_id",
            os.environ.get("DALSTON_DEFAULT_MODEL_ID", "openai/whisper-large-v3")
        )
        language = input.config.get("language")

        pipe = self._manager.acquire(model_id)
        try:
            # Run ASR pipeline
            generate_kwargs = {}
            if language and language != "auto":
                generate_kwargs["language"] = language

            result = pipe(
                str(input.audio_path),
                return_timestamps="word",
                generate_kwargs=generate_kwargs if generate_kwargs else None,
            )

            # Normalize output based on model type
            output = self._normalize_output(result, model_id, language)

            return TaskOutput(data=output)

        finally:
            self._manager.release(model_id)

    def _normalize_output(
        self,
        result: dict[str, Any],
        model_id: str,
        language: str | None,
    ) -> TranscribeOutput:
        """
        Normalize HuggingFace pipeline output to Dalston format.

        HF pipeline returns different formats based on model architecture:
        - Whisper: {"text": "...", "chunks": [{"text": "...", "timestamp": (start, end)}]}
        - Wav2Vec2: {"text": "..."}  # No timestamps by default
        - MMS: {"text": "..."}  # No timestamps
        """
        text = result.get("text", "").strip()
        chunks = result.get("chunks", [])

        segments = []
        all_words = []

        if chunks:
            # Process chunks with timestamps
            for i, chunk in enumerate(chunks):
                chunk_text = chunk.get("text", "").strip()
                timestamp = chunk.get("timestamp", (0, 0))
                start, end = timestamp if timestamp else (0, 0)

                # Build words from chunk
                words = []
                if "words" in chunk:
                    # Some models provide word-level in chunks
                    for w in chunk["words"]:
                        word = Word(
                            text=w.get("word", w.get("text", "")),
                            start=w.get("start", 0),
                            end=w.get("end", 0),
                            confidence=w.get("probability"),
                            alignment_method=AlignmentMethod.ATTENTION,
                        )
                        words.append(word)
                        all_words.append(word)

                segments.append(Segment(
                    id=f"seg_{i:03d}",
                    start=start or 0,
                    end=end or 0,
                    text=chunk_text,
                    words=words if words else None,
                ))
        else:
            # No timestamps - create single segment
            segments.append(Segment(
                id="seg_000",
                start=0.0,
                end=0.0,  # Unknown duration
                text=text,
            ))

        return TranscribeOutput(
            text=text,
            segments=segments,
            language=language or "auto",
            engine_id="hf-asr",
            timestamp_granularity_actual=(
                TimestampGranularity.WORD if all_words
                else TimestampGranularity.SEGMENT if chunks
                else TimestampGranularity.NONE
            ),
            alignment_method=AlignmentMethod.ATTENTION if chunks else None,
        )


if __name__ == "__main__":
    from dalston.engine_sdk.runner import EngineRunner
    engine = HFASREngine()
    runner = EngineRunner(engine)
    runner.run()
```

### docker-compose.yml Addition

```yaml
stt-batch-transcribe-hf-asr:
  image: dalston/engine-hf-asr:latest
  build:
    context: .
    dockerfile: engines/stt-transcribe/hf-asr/Dockerfile
  volumes:
    - model-cache:/models
  environment:
    <<: *common-env
    DALSTON_ENGINE_ID: hf-asr
    DALSTON_DEFAULT_MODEL_ID: openai/whisper-large-v3
  depends_on:
    - redis
    - minio
  profiles:
    - gpu  # HF transformers models are typically large
  deploy:
    resources:
      reservations:
        devices:
          - driver: nvidia
            count: 1
            capabilities: [gpu]
```

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

```dockerfile
# vLLM Audio ASR Engine
FROM vllm/vllm-openai:v0.6.0

# Install dalston engine-sdk
WORKDIR /opt/dalston
COPY pyproject.toml .
COPY dalston/ dalston/
RUN pip install --no-cache-dir -e ".[engine-sdk]"

# Install engine dependencies
WORKDIR /engine
COPY engines/stt-transcribe/vllm-asr/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy engine code
COPY engines/stt-transcribe/vllm-asr/ .
COPY engines/stt-transcribe/vllm-asr/engine.yaml /etc/dalston/engine.yaml

# Environment
ENV DALSTON_MODEL_DIR=/models
ENV HF_HUB_CACHE=/models/huggingface
ENV DALSTON_ENGINE_ID=vllm-asr

CMD ["python", "engine.py"]
```

### requirements.txt

```
vllm[audio]>=0.6.0
mistral-common[audio]>=1.5.0
soundfile>=0.12.0
librosa>=0.10.0
```

### engine.yaml

```yaml
id: vllm-asr
name: vLLM Audio ASR
version: 1.0.0
stage: transcribe
runtime: vllm-asr

description: |
  Audio-capable LLMs via vLLM for state-of-the-art transcription accuracy.
  Supports Voxtral, Qwen2-Audio, and future audio LLMs.
  Note: No word timestamps - use alignment stage if needed.

capabilities:
  languages: null  # Multilingual
  word_timestamps: false  # Audio LLMs don't produce timestamps
  punctuation: true
  streaming: false
  max_audio_duration_s: 600  # LLM context limits

hardware:
  gpu_required: true
  min_vram_gb: 8
  min_ram_gb: 16
  supports_cpu: false

performance:
  rtf_gpu: 0.15

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

### Base Adapter

**Create `adapters/base.py`:**

```python
"""Base adapter for audio LLM models."""
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from dalston.common.pipeline_types import TranscribeOutput, Segment


class AudioLLMAdapter(ABC):
    """Base class for model-specific prompt building and output parsing."""

    @abstractmethod
    def build_prompt(
        self,
        audio_path: Path,
        language: str | None = None,
    ) -> Any:
        """
        Build model-specific prompt with audio.

        Returns format expected by vLLM for the specific model.
        """
        raise NotImplementedError

    @abstractmethod
    def parse_output(
        self,
        raw_text: str,
        language: str | None = None,
    ) -> TranscribeOutput:
        """Parse model output to Dalston TranscribeOutput."""
        raise NotImplementedError
```

### Voxtral Adapter

**Create `adapters/voxtral.py`:**

```python
"""Voxtral-specific adapter for Mistral's audio LLM."""
from pathlib import Path
import base64

import soundfile as sf
from mistral_common.protocol.instruct.messages import (
    UserMessage, TextChunk, AudioChunk
)
from mistral_common.protocol.instruct.request import ChatCompletionRequest

from dalston.common.pipeline_types import TranscribeOutput, Segment
from .base import AudioLLMAdapter


class VoxtralAdapter(AudioLLMAdapter):
    """Adapter for Mistral Voxtral models."""

    LANGUAGE_PROMPTS = {
        "en": "Transcribe this audio in English.",
        "es": "Transcribe this audio in Spanish.",
        "fr": "Transcribe this audio in French.",
        "de": "Transcribe this audio in German.",
        None: "Transcribe this audio accurately.",
    }

    def build_prompt(
        self,
        audio_path: Path,
        language: str | None = None,
    ) -> ChatCompletionRequest:
        """Build Voxtral chat completion request with audio."""
        # Load audio
        audio, sr = sf.read(str(audio_path))

        # Create audio chunk using mistral-common
        audio_chunk = AudioChunk.from_array(audio, sample_rate=sr)

        # Build prompt
        prompt_text = self.LANGUAGE_PROMPTS.get(language, self.LANGUAGE_PROMPTS[None])

        return ChatCompletionRequest(
            messages=[
                UserMessage(content=[
                    audio_chunk,
                    TextChunk(text=prompt_text),
                ]),
            ],
        )

    def parse_output(
        self,
        raw_text: str,
        language: str | None = None,
    ) -> TranscribeOutput:
        """
        Parse Voxtral output.

        Voxtral returns plain text without timestamps.
        """
        text = raw_text.strip()

        return TranscribeOutput(
            text=text,
            segments=[
                Segment(
                    id="seg_000",
                    start=0.0,
                    end=0.0,  # No timing info from LLM
                    text=text,
                )
            ],
            language=language or "auto",
            engine_id="vllm-asr",
            # Important: No timestamps from audio LLMs
            timestamp_granularity_actual=None,
            alignment_method=None,
        )
```

### Engine Implementation

**Create `engine.py`:**

```python
"""vLLM Audio ASR engine for audio-capable LLMs."""
from __future__ import annotations
import os
from pathlib import Path

from vllm import LLM, SamplingParams
import structlog

from dalston.engine_sdk import Engine, TaskInput, TaskOutput
from dalston.engine_sdk.model_manager import ModelManager
from dalston.common.pipeline_types import TranscribeOutput

from adapters import get_adapter
from adapters.voxtral import VoxtralAdapter
from adapters.qwen2_audio import Qwen2AudioAdapter

logger = structlog.get_logger()


# Model to adapter mapping
MODEL_ADAPTERS = {
    "mistralai/Voxtral-Mini-3B-2507": VoxtralAdapter,
    "mistralai/Voxtral-Small-24B-2507": VoxtralAdapter,
    "Qwen/Qwen2-Audio-7B": Qwen2AudioAdapter,
}


class VLLMModelManager(ModelManager[LLM]):
    """Model manager for vLLM audio models."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    def _load_model(self, model_id: str) -> LLM:
        """Load vLLM model with audio support."""
        logger.info("loading_vllm_model", model_id=model_id)

        llm = LLM(
            model=model_id,
            trust_remote_code=True,
            # vLLM audio configuration
            limit_mm_per_prompt={"audio": 1},
        )

        logger.info("vllm_model_loaded", model_id=model_id)
        return llm

    def _unload_model(self, model: LLM) -> None:
        del model


class VLLMASREngine(Engine):
    """vLLM-based ASR for audio LLMs."""

    def __init__(self):
        super().__init__()

        self._manager = VLLMModelManager(
            ttl_seconds=int(os.environ.get("DALSTON_MODEL_TTL_SECONDS", 7200)),  # Longer TTL for large models
            max_loaded=int(os.environ.get("DALSTON_MAX_LOADED_MODELS", 1)),  # Usually only 1 fits
            preload=os.environ.get("DALSTON_MODEL_PRELOAD"),
        )

        # Cache adapters
        self._adapters: dict[str, Any] = {}

    def _get_adapter(self, model_id: str):
        """Get adapter for model."""
        if model_id not in self._adapters:
            adapter_class = MODEL_ADAPTERS.get(model_id)
            if adapter_class is None:
                raise ValueError(f"No adapter for model: {model_id}")
            self._adapters[model_id] = adapter_class()
        return self._adapters[model_id]

    def process(self, input: TaskInput) -> TaskOutput:
        model_id = input.config.get(
            "runtime_model_id",
            os.environ.get("DALSTON_DEFAULT_MODEL_ID", "mistralai/Voxtral-Mini-3B-2507")
        )
        language = input.config.get("language")

        adapter = self._get_adapter(model_id)
        llm = self._manager.acquire(model_id)

        try:
            # Build prompt with audio
            prompt = adapter.build_prompt(
                audio_path=Path(input.audio_path),
                language=language,
            )

            # Generate
            sampling_params = SamplingParams(
                temperature=0.0,  # Deterministic for transcription
                max_tokens=4096,
            )

            outputs = llm.generate([prompt], sampling_params)
            raw_text = outputs[0].outputs[0].text

            # Parse output
            result = adapter.parse_output(raw_text, language)

            return TaskOutput(data=result)

        finally:
            self._manager.release(model_id)


if __name__ == "__main__":
    from dalston.engine_sdk.runner import EngineRunner
    engine = VLLMASREngine()
    runner = EngineRunner(engine)
    runner.run()
```

### docker-compose.yml Addition

```yaml
stt-batch-transcribe-vllm-asr:
  image: dalston/engine-vllm-asr:latest
  build:
    context: .
    dockerfile: engines/stt-transcribe/vllm-asr/Dockerfile
  volumes:
    - model-cache:/models
  environment:
    <<: *common-env
    DALSTON_ENGINE_ID: vllm-asr
    DALSTON_DEFAULT_MODEL_ID: mistralai/Voxtral-Mini-3B-2507
    DALSTON_MODEL_TTL_SECONDS: 7200  # 2 hours - slow to load
    DALSTON_MAX_LOADED_MODELS: 1     # Large models
  depends_on:
    - redis
    - minio
  profiles:
    - gpu
  deploy:
    resources:
      reservations:
        devices:
          - driver: nvidia
            count: 1
            capabilities: [gpu]
  shm_size: '8gb'  # vLLM needs shared memory
```

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

Add new engines to `generated_catalog.json` or database seeding:

```json
{
  "engines": {
    "parakeet-onnx": {
      "id": "parakeet-onnx",
      "image": "dalston/engine-parakeet-onnx:latest",
      "stage": "transcribe",
      "runtime": "parakeet-onnx"
    },
    "hf-asr": {
      "id": "hf-asr",
      "image": "dalston/engine-hf-asr:latest",
      "stage": "transcribe",
      "runtime": "hf-asr"
    },
    "vllm-asr": {
      "id": "vllm-asr",
      "image": "dalston/engine-vllm-asr:latest",
      "stage": "transcribe",
      "runtime": "vllm-asr"
    }
  }
}
```

---

## Files Summary

### New Files

| File | Description |
|------|-------------|
| `engines/stt-transcribe/parakeet-onnx/*` | ONNX Parakeet engine |
| `engines/stt-transcribe/hf-asr/*` | HuggingFace Transformers engine |
| `engines/stt-transcribe/vllm-asr/*` | vLLM audio engine |
| `dalston/engine_sdk/managers/hf_transformers.py` | HF model manager |
| `dalston/engine_sdk/managers/vllm_audio.py` | vLLM model manager |

### Modified Files

| File | Change |
|------|--------|
| `docker-compose.yml` | Add 3 new engine services |
| `dalston/orchestrator/catalog.py` | Register new engines |
| `config/aliases.yaml` | Add aliases for new models |

---

## Verification

### Parakeet ONNX

```bash
# Build container
docker build -t dalston/engine-parakeet-onnx:latest \
  -f engines/stt-transcribe/parakeet-onnx/Dockerfile .

# Test locally
docker run --rm -it -v model-cache:/models \
  dalston/engine-parakeet-onnx:latest python -c "
from engine import ParakeetOnnxEngine
engine = ParakeetOnnxEngine()
# Should load model and show available providers
"

# Compare vs NeMo version
# Transcribe same file, compare accuracy and timing
```

### HF-ASR

```bash
# Test with Whisper
dalston model pull openai/whisper-large-v3
curl -X POST http://localhost:8000/v1/audio/transcriptions \
  -H "Authorization: Bearer dk_test" \
  -F "file=@test.wav" \
  -F "model=openai/whisper-large-v3"

# Test with Wav2Vec2
dalston model pull facebook/wav2vec2-large-960h
curl -X POST http://localhost:8000/v1/audio/transcriptions \
  -F "file=@test.wav" \
  -F "model=facebook/wav2vec2-large-960h"

# Test with MMS (1000+ languages)
dalston model pull facebook/mms-1b-all
curl -X POST http://localhost:8000/v1/audio/transcriptions \
  -F "file=@croatian.wav" \
  -F "model=facebook/mms-1b-all" \
  -F "language=hr"
```

### vLLM-ASR

```bash
# Requires GPU with 8GB+ VRAM
dalston model pull mistralai/Voxtral-Mini-3B-2507

# Test transcription (note: no timestamps)
curl -X POST http://localhost:8000/v1/audio/transcriptions \
  -F "file=@test.wav" \
  -F "model=voxtral-mini-3b"

# Test with alignment for timestamps
curl -X POST http://localhost:8000/v1/audio/transcriptions \
  -F "file=@test.wav" \
  -F "model=voxtral-mini-3b" \
  -F "timestamps_granularity=word"
# Should chain: vllm-asr → phoneme-align
```

---

## Checkpoint

- [ ] **41.1**: Parakeet ONNX engine working
- [ ] **41.1**: ONNX engine smaller than NeMo (~1GB vs ~5GB)
- [ ] **41.1**: Same accuracy as NeMo version
- [ ] **41.2**: HF-ASR engine loads transformers models
- [ ] **41.2**: Tested with Whisper, Wav2Vec2, MMS
- [ ] **41.2**: Output normalized to Dalston format
- [ ] **41.3**: vLLM-ASR engine loads Voxtral
- [ ] **41.3**: Adapters work for different model families
- [ ] **41.3**: Alignment chaining works when timestamps needed
- [ ] All engines use TTL-based ModelManager
- [ ] Catalog updated with new engines

**Next**: Further engine optimizations or production deployment
