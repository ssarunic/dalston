# M32: Engine Variant Structure

|                  |                                                                    |
| ---------------- | ------------------------------------------------------------------ |
| **Goal**         | Treat model sizes as separate deployable engines with shared code  |
| **Duration**     | 1.5-2 days                                                         |
| **Dependencies** | M30 (Engine Metadata Evolution)                                    |
| **Deliverable**  | Restructured engines, updated tooling, accurate per-variant specs  |
| **Status**       | Complete                                                           |

## User Story

> *"As a platform operator, I want each model size to have accurate hardware requirements so I can right-size my infrastructure and scale variants independently."*

> *"As a contributor, I want to add a new model variant by creating a single YAML file without duplicating engine code."*

---

## Context

Model families have multiple size variants with different hardware requirements. Currently, engines like `faster-whisper` handle all sizes via `config.model` parameter, which prevents:

- Accurate hardware requirements per size
- Independent scaling of variants
- Right-sized container images

This milestone restructures engines so each variant is a separate deployable unit with shared implementation code.

See [ADR-010: Engine Variant Structure](../../decisions/ADR-010-engine-variant-structure.md) for the full decision rationale.

---

## Target Structure

### Before

```
engines/transcribe/faster-whisper/
├── engine.yaml          # config_schema lists all sizes
├── engine.py
├── Dockerfile
└── requirements.txt
```

### After

```
engines/transcribe/whisper/
├── engine.py            # Shared implementation
├── Dockerfile           # Parameterized (ARG MODEL_SIZE)
├── requirements.txt
└── variants/
    ├── base.yaml        # id: whisper-base, VRAM: 2GB
    ├── large-v3.yaml    # id: whisper-large-v3, VRAM: 6GB
    └── large-v3-turbo.yaml  # id: whisper-large-v3-turbo, VRAM: 4GB
```

---

## Variants to Ship

We ship commonly-used variants. Additional variants can be added by users.

### Whisper (Batch)

| Variant | VRAM | GPU | CPU Support | Use Case |
|---------|------|-----|-------------|----------|
| whisper-base | 2GB | T4 | Yes | Fast, good enough |
| whisper-large-v3 | 6GB | A10G | No | Best accuracy |
| whisper-large-v3-turbo | 4GB | T4/A10G | No | Fast + accurate |

### Parakeet (Batch)

| Variant | VRAM | GPU | Use Case |
|---------|------|-----|----------|
| parakeet-0.6b | 4GB | T4 | Fast English |
| parakeet-1.1b | 6GB | A10G | Accurate English |

### Whisper Streaming (Realtime)

| Variant | Use Case |
|---------|----------|
| whisper-streaming-base | Low-latency realtime |

### Parakeet Streaming (Realtime)

| Variant | Use Case |
|---------|----------|
| parakeet-streaming-0.6b | Fast English streaming |
| parakeet-streaming-1.1b | Accurate English streaming |

---

## Steps

### 32.1: Update generate_catalog.py

Modify catalog generation to scan `variants/*.yaml` in addition to `engine.yaml`.

```python
def find_engine_yamls(engines_dir: Path) -> list[Path]:
    """Find all engine.yaml and variant files."""
    yamls = list(engines_dir.glob("**/engine.yaml"))
    yamls += list(engines_dir.glob("**/variants/*.yaml"))
    return sorted(yamls)
```

**Files:**

- MODIFY: `scripts/generate_catalog.py`

**Tests:**

- MODIFY: `tests/unit/test_generate_catalog.py`

---

### 32.2: Update scaffold_engine.py

Add `--variants` flag to generate variant structure.

```bash
# Generate engine with variant structure
python -m dalston.tools.scaffold_engine whisper --stage transcribe \
    --variants base,large-v3,large-v3-turbo

# Creates:
# engines/transcribe/whisper/
# ├── engine.py
# ├── Dockerfile
# ├── requirements.txt
# └── variants/
#     ├── base.yaml
#     ├── large-v3.yaml
#     └── large-v3-turbo.yaml
```

**Files:**

- MODIFY: `dalston/tools/scaffold_engine.py`

---

### 32.3: Restructure Whisper Engine

Create new structure with three variants.

**New files:**

- `engines/transcribe/whisper/engine.py`
- `engines/transcribe/whisper/Dockerfile`
- `engines/transcribe/whisper/requirements.txt`
- `engines/transcribe/whisper/variants/base.yaml`
- `engines/transcribe/whisper/variants/large-v3.yaml`
- `engines/transcribe/whisper/variants/large-v3-turbo.yaml`

**Delete:**

- `engines/transcribe/faster-whisper/` (entire directory)

#### Parameterized Dockerfile

```dockerfile
ARG MODEL_SIZE=large-v3

FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/dalston
COPY pyproject.toml .
COPY dalston/ dalston/
RUN pip install --no-cache-dir -e ".[engine-sdk]"

WORKDIR /engine
COPY engines/transcribe/whisper/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY engines/transcribe/whisper/engine.py .
COPY engines/transcribe/whisper/variants/${MODEL_SIZE}.yaml /etc/dalston/engine.yaml

ENV WHISPER_MODEL=${MODEL_SIZE}
ENV HF_HOME=/models
RUN mkdir -p /models

# Pre-download model for faster cold start
RUN python -c "from faster_whisper import WhisperModel; WhisperModel('${MODEL_SIZE}', download_root='/models')"

CMD ["python", "engine.py"]
```

#### Shared engine.py

```python
import os
from faster_whisper import WhisperModel
from dalston.engine_sdk import Engine, TaskInput, TaskOutput

class WhisperEngine(Engine):
    def __init__(self):
        super().__init__()
        self._model_size = os.environ.get("WHISPER_MODEL", "large-v3")
        self._model = None

    def process(self, input: TaskInput) -> TaskOutput:
        if self._model is None:
            self._model = WhisperModel(self._model_size, ...)
        # ... transcription logic (same as current faster-whisper)
```

#### Example variant YAML

```yaml
# variants/large-v3.yaml
schema_version: "1.1"
id: whisper-large-v3
stage: transcribe
name: Whisper Large V3
version: 1.0.0
description: |
  OpenAI Whisper large-v3 via faster-whisper (CTranslate2).
  Best accuracy for multilingual transcription.

container:
  gpu: required
  memory: 8G
  model_cache: /models

capabilities:
  languages:
    - all
  max_audio_duration: 7200
  streaming: false
  word_timestamps: false
  max_concurrency: 4

input:
  audio_formats:
    - wav
  sample_rate: 16000
  channels: 1

hf_compat:
  pipeline_tag: automatic-speech-recognition
  library_name: ctranslate2
  license: mit
  source_model: "Systran/faster-whisper-large-v3"

hardware:
  min_vram_gb: 6
  recommended_gpu:
    - a10g
    - a100
  supports_cpu: false
  min_ram_gb: 16

performance:
  rtf_gpu: 0.05
  rtf_cpu: null
  warm_start_latency_ms: 50
```

---

### 32.4: Restructure Parakeet Engine

Same pattern as Whisper with two variants (0.6b, 1.1b).

**New files:**

- `engines/transcribe/parakeet/engine.py` (refactored)
- `engines/transcribe/parakeet/Dockerfile` (parameterized)
- `engines/transcribe/parakeet/variants/0.6b.yaml`
- `engines/transcribe/parakeet/variants/1.1b.yaml`

**Delete:**

- `engines/transcribe/parakeet/engine.yaml` (replaced by variants)

---

### 32.5: Restructure Realtime Engines

Apply same pattern to realtime engines.

**Whisper Streaming:**

- `engines/realtime/whisper-streaming/variants/base.yaml`

**Parakeet Streaming:**

- `engines/realtime/parakeet-streaming/variants/0.6b.yaml`
- `engines/realtime/parakeet-streaming/variants/1.1b.yaml`

---

### 32.6: Update docker-compose.yml

Replace single services with per-variant services.

```yaml
# Before
stt-batch-transcribe-whisper:
  build:
    context: .
    dockerfile: engines/transcribe/faster-whisper/Dockerfile
  environment:
    - ENGINE_ID=faster-whisper

# After
stt-batch-transcribe-whisper-base:
  build:
    context: .
    dockerfile: engines/transcribe/whisper/Dockerfile
    args:
      MODEL_SIZE: base
  environment:
    - ENGINE_ID=whisper-base
  profiles: [whisper-base]

stt-batch-transcribe-whisper-large-v3:
  build:
    context: .
    dockerfile: engines/transcribe/whisper/Dockerfile
    args:
      MODEL_SIZE: large-v3
  environment:
    - ENGINE_ID=whisper-large-v3

stt-batch-transcribe-whisper-large-v3-turbo:
  build:
    context: .
    dockerfile: engines/transcribe/whisper/Dockerfile
    args:
      MODEL_SIZE: large-v3-turbo
  environment:
    - ENGINE_ID=whisper-large-v3-turbo
  profiles: [whisper-turbo]
```

Use profiles for optional variants. Default startup includes `whisper-large-v3` and `parakeet-0.6b`.

---

### 32.7: Simplify MODEL_SELECTION.md

Model ID now equals engine ID. Remove `engine_model` indirection.

```python
# Before
"whisper-large-v3": ModelDefinition(
    engine="faster-whisper",
    engine_model="large-v3",
)

# After
"whisper-large-v3": ModelDefinition(
    engine="whisper-large-v3",  # Direct mapping
)
```

Keep aliases:

```python
ALIASES = {
    "fast": "whisper-base",
    "accurate": "whisper-large-v3",
    "turbo": "whisper-large-v3-turbo",
}
```

---

### 32.8: Update Documentation

Update `docs/specs/batch/ENGINES.md` with the variant pattern.

Add section:

```markdown
## Engine Variants

When a model family has multiple sizes with different hardware requirements,
create separate YAML files for each variant:

    engines/transcribe/whisper/
    ├── engine.py                    # Shared implementation
    ├── Dockerfile                   # Parameterized (ARG MODEL_SIZE)
    └── variants/
        ├── base.yaml                # id: whisper-base
        └── large-v3.yaml            # id: whisper-large-v3

Each variant is a separate deployable engine with accurate hardware specs.
To add a new variant, create a YAML file and add a docker-compose service.
```

---

## Verification

```bash
# 1. Validate all variant YAMLs
python -m dalston.tools.validate_engine --all
# Should validate: whisper-base, whisper-large-v3, whisper-large-v3-turbo,
#                  parakeet-0.6b, parakeet-1.1b, ...

# 2. Generate catalog
python scripts/generate_catalog.py --dry-run | jq '.engines | keys'
# ["audio-prepare", "parakeet-0.6b", "parakeet-1.1b", "whisper-base",
#  "whisper-large-v3", "whisper-large-v3-turbo", ...]

# 3. Build specific variant
docker compose build stt-batch-transcribe-whisper-large-v3

# 4. Run and test
docker compose up -d gateway orchestrator redis stt-batch-transcribe-whisper-large-v3

curl -X POST http://localhost:8000/v1/audio/transcriptions \
  -H "Authorization: Bearer dk_test" \
  -F "file=@test.wav" \
  -F "model=whisper-large-v3"

# 5. Verify engine registers with correct ID
docker compose logs stt-batch-transcribe-whisper-large-v3 | grep "engine_registered"
# Should show engine_id=whisper-large-v3
```

---

## Files Changed

| File | Change |
|------|--------|
| `scripts/generate_catalog.py` | Scan variants/*.yaml |
| `dalston/tools/scaffold_engine.py` | Add --variants flag |
| `engines/transcribe/faster-whisper/` | DELETE |
| `engines/transcribe/whisper/` | NEW (with variants/) |
| `engines/transcribe/parakeet/` | RESTRUCTURE (add variants/) |
| `engines/realtime/whisper-streaming/` | RESTRUCTURE |
| `engines/realtime/parakeet-streaming/` | RESTRUCTURE |
| `docker-compose.yml` | Per-variant services |
| `docs/specs/batch/ENGINES.md` | Document variant pattern |
| `docs/specs/MODEL_SELECTION.md` | Simplify registry |
| `docs/decisions/ADR-010-engine-variant-structure.md` | NEW |

---

## Not In Scope

- Adding new models (Voxtral, Qwen3) - separate milestone
- Changing orchestrator routing logic - not needed
- Changing API contract - model parameter works identically
- Shipping all Whisper variants (tiny, small, medium, large-v1, large-v2) - users can add if needed

---

## Implementation Order

| Step | Scope | Effort |
|------|-------|--------|
| 32.1 | Update generate_catalog.py | 30 min |
| 32.2 | Update scaffold_engine.py | 1 hour |
| 32.3 | Restructure Whisper engine | 2 hours |
| 32.4 | Restructure Parakeet engine | 1 hour |
| 32.5 | Restructure realtime engines | 1 hour |
| 32.6 | Update docker-compose.yml | 1 hour |
| 32.7 | Simplify MODEL_SELECTION.md | 30 min |
| 32.8 | Update documentation | 30 min |

**Total: ~8 hours (1-1.5 days)**

---

## Enables Next

- **M33: Voxtral Engine** - New engine follows variant pattern from start
- **M34: Qwen3-ASR Engine** - New engine follows variant pattern from start
- **Auto-provisioning** - `dalston engine add hf://...` generates variant structure
- **Cost attribution** - Accurate per-variant resource tracking
