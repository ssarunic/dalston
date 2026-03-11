# ADR-013: Inference Architecture — Unified Runner Default, Sidecar Deferred

## Status

Accepted

## Context

Dalston has two patterns for sharing a GPU-loaded model between batch and
real-time processing:

1. **Unified runner (M63)** — One container runs both batch (Redis queue thread)
   and RT (async WebSocket) adapters in a single process, sharing an in-process
   `TranscribeCore`. Proven in production for Parakeet; faster-whisper runner
   implemented but not yet containerized.

2. **gRPC inference server sidecar (M72)** — A standalone gRPC server owns the
   GPU model. Batch and RT engines become thin CPU-only adapters calling
   `.transcribe()` over the network. Implemented for faster-whisper and parakeet
   but not yet deployed as the default path.

M72 was motivated by fault isolation (adapter crash doesn't unload the model)
and independent scaling (scale batch adapters separately from RT). However, it
introduced significant complexity:

- ~3,000 lines of new code (proto definitions, gRPC server base, remote core,
  two server implementations)
- 3 containers per model instead of 1
- New dependencies (grpcio, protobuf)
- Distributed debugging across adapter ↔ server boundary
- Health checking and reconnection logic between components

For a self-hosted system that currently runs on a single machine with 1–2 GPUs,
the sidecar pattern solves a scaling problem that doesn't yet exist.

## Options Considered

### 1. Unified runner as default (M63 pattern)

One container per model. Batch and RT share an in-process core with admission
control gating concurrency.

```
┌────────────────────────────────────┐
│       Unified Container (GPU)      │
│  Batch (thread) │ RT (async)       │
│         └───┬───┘                  │
│      AdmissionController           │
│      TranscribeCore (in-process)   │
└────────────────────────────────────┘
```

**Pros:** Zero network overhead, 1 container, simple operations, already works
for Parakeet.
**Cons:** Coupled failure domain, can't scale batch/RT independently, process
restart drops RT sessions and reloads model.

### 2. gRPC sidecar as default (M72 pattern)

Three containers per model. Inference server on GPU, adapters on CPU.

```
┌──────────────┐  gRPC  ┌──────────────────┐
│ Batch adapter│───────▶│ Inference Server  │
└──────────────┘        │ (GPU, model)      │
┌──────────────┐  gRPC  │                   │
│ RT adapter   │───────▶│                   │
└──────────────┘        └──────────────────┘
```

**Pros:** Fault isolation, independent scaling, model survives adapter restarts.
**Cons:** 3× containers, gRPC latency (1–5ms), distributed debugging, routing
and health-check complexity.

### 3. Universal merged adapter + inference servers (future evolution)

One adapter container (batch + RT) routing to N inference servers by model_id.
M+1 containers total instead of M×2 or M×3.

```
┌──────────────────────┐        ┌─────────────────┐
│  Universal Adapter   │─gRPC──▶│ whisper-server   │
│  Batch + RT + routing│─gRPC──▶│ parakeet-server  │
└──────────────────────┘        └─────────────────┘
```

**Pros:** Fewest containers, single I/O codebase, clean separation of concerns.
**Cons:** Model-specific output normalization bleeds into adapter or servers,
routing registry required, WebSocket session stickiness for HA.

## Decision

**Use unified runners as the default deployment for all runtimes.** The sidecar
pattern (M72) is retained as code but marked as an optional scaling path, not
the primary architecture.

Rationale:

- The unified runner for faster-whisper is already implemented (`runner.py`) and
  only needs containerization (Dockerfile + requirements.txt + compose entries)
  to be deployable.
- For single-machine / single-GPU deployments, the operational simplicity of one
  container per model outweighs the fault isolation benefits of the sidecar.
- The sidecar code (`inference_server.py`, `remote_core.py`, `stt-server/*`)
  remains available for multi-node deployments where independent scaling becomes
  necessary.

**When to switch to sidecar:** If any of these become true:
- Running across multiple machines (GPU nodes separate from CPU nodes)
- Frequent adapter crashes where model reload time (30–60s) is unacceptable
- Need to scale batch throughput independently of RT session count
- Moving to Kubernetes (sidecar maps directly to pod sidecar pattern)

**Future direction:** If the sidecar pattern is adopted, evolve toward a
universal merged adapter (option 3) rather than per-model adapter pairs. This
requires inference servers to return Dalston-schema responses (not raw model
output) so the adapter stays model-agnostic.

## Consequences

### Easier

- Fewer containers to deploy, monitor, and debug (1 per model vs 3)
- Adding a new model = one Dockerfile + compose entry
- No gRPC proto versioning concerns for the default path
- Simpler local development (`make dev` starts fewer services)

### Harder

- Batch and RT share a failure domain — process crash affects both
- Cannot independently scale batch throughput vs RT session count
- Process restart requires model reload (~5–30s depending on cache warmth)
- Must complete unified runner containerization for faster-whisper (minor effort)
