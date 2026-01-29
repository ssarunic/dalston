# ADR-002: Engine Isolation via Docker Containers

## Status

Accepted

## Context

Dalston uses multiple ML models and processing libraries:

- **faster-whisper**: CTranslate2-based Whisper implementation
- **WhisperX**: Whisper + wav2vec2 alignment + pyannote
- **pyannote**: Speaker diarization (torch-based)
- **emotion2vec**: Emotion detection
- **Various LLM clients**: For text cleanup

These libraries have conflicting dependencies:
- Different PyTorch versions
- Different CUDA requirements
- Incompatible numpy/scipy versions
- Some require specific Python versions

Running all engines in a single process or virtualenv is impractical.

## Options Considered

### 1. Single Process with Careful Dependency Management

Pin all dependencies to compatible versions, accept feature limitations.

**Pros:**
- Simpler deployment (one container)
- No inter-process communication overhead
- Easier debugging

**Cons:**
- May be impossible (some conflicts are irreconcilable)
- Limits which models/versions can be used
- Upgrades become very difficult
- Single point of failure

### 2. Separate Python Virtual Environments

Run each engine in its own venv, communicate via filesystem or IPC.

**Pros:**
- Isolates Python dependencies
- No container overhead
- Shared GPU memory pool

**Cons:**
- Doesn't isolate system libraries
- Complex venv management
- Still shares CUDA runtime (version conflicts)
- Process management complexity

### 3. Docker Containers per Engine (Chosen)

Each engine runs in its own container with its own dependencies.

**Pros:**
- Complete isolation (Python, system libs, CUDA)
- Each engine can use optimal dependencies
- Independent scaling per engine type
- Clear API boundaries (queue in, result out)
- Reproducible builds

**Cons:**
- Container overhead (minimal for long-running workers)
- GPU memory not shared (each container reserves its allocation)
- More complex orchestration
- Larger disk footprint (multiple images)

### 4. Kubernetes Pods with Sidecars

Engines as sidecar containers in pods, sharing network namespace.

**Pros:**
- Fine-grained resource control
- Native Kubernetes orchestration
- Health checks and restarts

**Cons:**
- Requires Kubernetes (complex for self-hosted)
- Over-engineered for single-node deployments
- Steep learning curve

## Decision

Use Docker containers for engine isolation:

1. **Each engine type gets its own Docker image** with exactly the dependencies it needs
2. **Communication via Redis queues** — loose coupling, no direct container networking needed
3. **Artifacts via S3** — containers don't share filesystem
4. **GPU access via NVIDIA Container Toolkit** — `--gpus` flag for GPU-enabled engines

### Container Design Principles

```
┌─────────────────────────────────────────┐
│           Engine Container              │
│                                         │
│  ┌─────────────────────────────────┐   │
│  │       Engine SDK (shared)        │   │
│  │  • Queue polling                 │   │
│  │  • S3 I/O                        │   │
│  │  • Health reporting              │   │
│  └─────────────────────────────────┘   │
│                                         │
│  ┌─────────────────────────────────┐   │
│  │    Engine Implementation         │   │
│  │  • Model loading                 │   │
│  │  • Processing logic              │   │
│  │  • Engine-specific dependencies  │   │
│  └─────────────────────────────────┘   │
│                                         │
└─────────────────────────────────────────┘
```

- **Engine SDK**: Shared library handling queue interaction, installed in all engine images
- **Engine Implementation**: Specific to each engine, with its own requirements.txt

### GPU Memory Management

Since containers don't share GPU memory:
- Configure `CUDA_VISIBLE_DEVICES` to assign specific GPUs
- Use model size appropriate for available VRAM
- Consider model unloading for memory-constrained setups

## Consequences

### Easier

- Adding new engines (just create new Dockerfile)
- Upgrading individual engines (no dependency conflicts)
- Debugging (isolated environment, clear boundaries)
- Scaling specific bottlenecks (run more of one engine type)

### Harder

- Local development without Docker
- GPU memory efficiency (can't share models across engines)
- Initial setup (multiple images to build)
- CI/CD pipeline (multiple image builds)

### Mitigations

- Provide `docker-compose.yml` for easy local setup
- Document GPU memory requirements per engine
- Use multi-stage builds to reduce image sizes
- Cache base images in CI
