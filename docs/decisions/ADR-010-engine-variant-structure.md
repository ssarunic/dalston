# ADR-010: Engine Variant Structure

## Status

Accepted

## Context

Model families like Whisper, Parakeet, Voxtral, and Qwen have multiple size variants with significantly different hardware requirements:

| Model | VRAM | RTF (GPU) | Use Case |
|-------|------|-----------|----------|
| whisper-tiny | 1GB | 0.02 | Fast drafts, low-resource |
| whisper-base | 2GB | 0.03 | Balanced speed/quality |
| whisper-large-v3 | 6GB | 0.05 | Best accuracy |
| whisper-large-v3-turbo | 4GB | 0.03 | Fast + accurate |

Currently, `faster-whisper` handles all sizes via `config.model` parameter. This creates problems:

1. **Inaccurate hardware specs.** The engine.yaml must specify worst-case requirements (6GB VRAM), even when running tiny (1GB).

2. **No independent scaling.** Cannot scale tiny instances separately from large instances. A deployment needing 10x tiny throughput must provision 10x large-capable machines.

3. **Coarse cost attribution.** All jobs charged at "large" rate regardless of actual model used.

4. **Container bloat.** Single container sized for largest model, even when only small models are needed.

The session router already treats each realtime worker variant as a separate entity. Batch engines should follow the same pattern.

## Decision

Treat each model size as a separate deployable engine with shared implementation code.

### Directory Structure

```
engines/transcribe/whisper/
├── engine.py                    # Shared implementation
├── Dockerfile                   # Parameterized via ARG
├── requirements.txt
└── variants/
    ├── base.yaml                # id: whisper-base
    ├── large-v3.yaml            # id: whisper-large-v3
    └── large-v3-turbo.yaml      # id: whisper-large-v3-turbo
```

### Variant engine.yaml

Each variant specifies accurate hardware requirements:

```yaml
# variants/base.yaml
schema_version: "1.1"
id: whisper-base
stage: transcribe
name: Whisper Base

hardware:
  min_vram_gb: 2
  recommended_gpu: [t4]
  supports_cpu: true

performance:
  rtf_gpu: 0.03
  rtf_cpu: 0.4
```

```yaml
# variants/large-v3.yaml
schema_version: "1.1"
id: whisper-large-v3
stage: transcribe
name: Whisper Large V3

hardware:
  min_vram_gb: 6
  recommended_gpu: [a10g, a100]
  supports_cpu: false

performance:
  rtf_gpu: 0.05
  rtf_cpu: null
```

### Parameterized Dockerfile

```dockerfile
ARG MODEL_SIZE=large-v3
FROM python:3.11-slim
# ... common setup ...
ENV WHISPER_MODEL=${MODEL_SIZE}
```

### Shared engine.py

```python
import os

class WhisperEngine(Engine):
    def __init__(self):
        super().__init__()
        # Model determined by container, not request config
        self._model_size = os.environ["WHISPER_MODEL"]
```

### Catalog Generation

`generate_catalog.py` scans both `engine.yaml` files and `variants/*.yaml`:

```python
def find_engine_yamls(engines_dir: Path) -> list[Path]:
    yamls = list(engines_dir.glob("**/engine.yaml"))
    yamls += list(engines_dir.glob("**/variants/*.yaml"))
    return sorted(yamls)
```

## Consequences

### Benefits

1. **Accurate resource allocation.** Each variant declares true requirements. Orchestrator and auto-scaler make correct decisions.

2. **Independent scaling.** Scale whisper-base to 10 instances while keeping whisper-large-v3 at 2.

3. **Right-sized containers.** Base variant container is smaller (fewer dependencies, smaller model cache).

4. **Simplified model selection.** User requests `model=whisper-large-v3`, which maps directly to engine ID. No engine_model indirection needed.

5. **Extensibility.** Adding a new variant is one YAML file + docker-compose entry. No code changes.

### Costs

1. **More catalog entries.** Catalog grows from ~10 engines to ~20. Acceptable given the benefits.

2. **More docker-compose services.** Each variant is a separate service. Mitigated by profiles for optional variants.

3. **Shared code management.** Engine implementation shared across variants. Standard pattern (parameterized Dockerfile) handles this.

### Migration

- Existing engines (`faster-whisper`, `parakeet`) restructured to new pattern
- API unchanged - `model` parameter works identically
- No orchestrator changes required - it already routes by engine_id

## Alternatives Considered

### Keep Single Engine with Config Parameter

Status quo. Rejected because hardware requirements cannot be accurately specified, and independent scaling is impossible.

### Engine Families (Grouped Variants)

Group tiny/base/small into "whisper-light", medium/large into "whisper-heavy". Rejected because:

- Arbitrary groupings
- Still has "max of group" sizing problem
- More complex to explain than "one engine = one model"

## References

- [M32: Engine Variant Structure](../plan/milestones/M32-engine-variant-structure.md)
- [M30: Engine Metadata Evolution](../plan/milestones/M30-engine-metadata-evolution.md)
- Industry pattern: Replicate, Modal, HuggingFace Inference all treat model sizes as separate deployments
