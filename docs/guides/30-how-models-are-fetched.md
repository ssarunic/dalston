# How models are fetched and cached

> Engines can pull weights from your S3 bucket, HuggingFace Hub, or both.
> `make dev` and `dalston-aws` default to `auto` for a smooth first run: S3
> first, HuggingFace fallback. For production repeatability, switch to strict
> `s3` after you pre-stage models. Either way, on-disk caching makes repeat
> launches much faster.

This page is the principles deep-dive. If you just want to run an engine,
[11-single-engine-tailscale-mode.md](11-single-engine-tailscale-mode.md) and
[12-engine-presets-catalog.md](12-engine-presets-catalog.md) are the
practical pages.

---

## Two sources, configurable

`DALSTON_MODEL_SOURCE` controls the lookup order. Source:
[`dalston/engine_sdk/model_storage.py`](../../dalston/engine_sdk/model_storage.py).

| Value | Behavior |
|---|---|
| `s3` | Only S3. Requires `DALSTON_S3_BUCKET`; misses fail fast. |
| `hf` | Only HuggingFace Hub. Requires `HF_TOKEN` for gated models. |
| `ngc` | NVIDIA NGC (stub, not yet wired). |
| `auto` | Try local cache → S3 → HF → NGC, first one that works. |

The engine SDK falls back to `s3` if the environment variable is unset, but the
user-facing workflows set `auto` because new buckets are empty. Most production
deployments should set `DALSTON_MODEL_SOURCE=s3` after they pre-stage models,
because a missing model should be an explicit operational error.

```bash
# First-run default for make dev and dalston-aws:
DALSTON_MODEL_SOURCE=auto

# Hardened production mode after S3 is populated:
DALSTON_MODEL_SOURCE=s3
```

Path layouts:

```
S3:      s3://{bucket}/models/{model_id}/
              model.bin
              config.json
              ...
              .complete           # marker: upload finished

Local:   /models/{model_id}/
              model.bin
              ...
              .complete           # marker: download finished

HF:      $HF_HOME/hub/...         # huggingface_hub native cache
```

The `.complete` marker file is the trick — it's only written *after* every
file is in place. An engine that crashes mid-download leaves an incomplete
directory; the next start sees no marker, deletes the partial copy, and
re-downloads. No half-cached models.

---

## What happens on first launch

Take `nemo` as an example. The container starts and:

1. Looks at `DALSTON_DEFAULT_MODEL` (set to `nvidia/parakeet-tdt-0.6b-v3` by
   the preset).
2. Checks `/data/models/nvidia/parakeet-tdt-0.6b-v3/.complete` — not there.
3. If `DALSTON_MODEL_SOURCE=auto` (the `make dev` / `dalston-aws` default),
   tries S3 (`s3://dalston-artifacts/models/nvidia/parakeet-tdt-0.6b-v3/`).
   If the bucket has the model, downloads in parallel from S3, ~30 seconds.
4. On an S3 miss, falls through to HuggingFace Hub
   (`huggingface_hub.snapshot_download(repo_id="nvidia/parakeet-tdt-0.6b-v3")`).
   Authentication: `HF_TOKEN` for gated models.
5. If `DALSTON_MODEL_SOURCE=s3`, the same miss fails fast instead of falling
   through.
6. Writes `.complete`. Loads the model into VRAM. Health flips to `ok`.

Cold-start budgets per engine (verified from each `engine.yaml`
`performance.warm_start_latency_ms`, plus actual download size):

| Engine | First-run download | Warm start (cache hit) |
|---|---|---|
| `onnx` | ~1 GB | 50 ms |
| `faster-whisper` (large-v3-turbo) | ~3 GB | 30 ms |
| `nemo` (Parakeet TDT 0.6B-v3) | ~2.5 GB | 100 ms |
| `pyannote-4.0` (Community-1) | ~600 MB | 500 ms |
| `vllm-asr` (Voxtral Mini 3B) | ~6 GB | 5000 ms |
| `hf-asr` (Whisper large-v3) | ~3 GB | 500 ms |

> **Why warm start matters:** if your GPU worker gets reclaimed and the cache
> survives (split mode), warm start brings the engine back in tens of
> milliseconds to a few seconds. If the cache is gone (single-engine mode),
> you pay the first-run download price every reclaim. This is a real cost
> argument for split mode.

---

## Where the cache lives on disk

Each engine declares its mount in `engine.yaml`:

| Engine | `model_cache` |
|---|---|
| `onnx` | `/models/onnx` |
| `faster-whisper` | `/models/faster-whisper` |
| `nemo` | `/models/nemo` |
| `pyannote-4.0` | `/models` |
| `hf-asr`, `hf-asr-align-pyannote`, `vllm-asr` | `/models/huggingface` |

In `make dev`, these are Docker named volumes (one per service).
In `dalston-aws` control-plane services, CPU-engine caches map under
`/data/models` on the control plane's persistent EBS. Separate GPU workers map
their engine caches to worker-local `/data/models`, which is lost when a spot
worker is terminated. For GPU workers, S3 is the durable model cache across
replacement instances.

In single-engine mode (`engine up`), `/data` is on the worker's ephemeral
EBS (deleted with the instance). Cache is lost on terminate. By design.

---

## HF_TOKEN — when you actually need it

Set `HF_TOKEN` only for **gated** HuggingFace models. The big one in this
repo is **pyannote** — you must accept the model's license at
<https://huggingface.co/pyannote/speaker-diarization-community-1> before
the token can download it. The engine raises a clear error if
`HF_TOKEN` is missing
([`engines/stt-diarize/pyannote-4.0/engine.py:73`](../../engines/stt-diarize/pyannote-4.0/engine.py#L73)):

```python
raise RuntimeError(
    "HF_TOKEN environment variable is required for pyannote diarization. "
    "Get a token from https://huggingface.co/settings/tokens and accept "
    "the pyannote/speaker-diarization-community-1 model agreement."
)
```

Other engines mostly use ungated models (Whisper, Parakeet, ONNX exports,
Wav2Vec2) and don't need the token.

How to set it:

```bash
# Local
export HF_TOKEN=hf_...
make dev

# AWS (passed through user-data → docker-compose env)
export HF_TOKEN=hf_...
dalston-aws engine up pyannote --spot
```

The `dalston-aws` script reads `HF_TOKEN` from your shell at `engine up`
time and forwards it to the EC2 instance via user-data.

---

## Pre-loading models into S3 (production speedup)

In production, you typically pre-stage models in S3 once. Then every engine
launch in your account hits S3 (faster, no rate limits, costs nothing) and
HuggingFace is only used as a fallback.

The simplest way: launch an engine on a dev box, let it pull from HF the
first time, then mirror `/data/models/...` to S3:

```bash
# On the engine box
aws s3 sync /data/models s3://your-bucket/models/ \
  --exclude "*.tmp" --exclude "*.lock"
```

After that, set `DALSTON_MODEL_SOURCE=s3` so engines hit S3 only and
HuggingFace is never reached on routine launches. Privacy bonus: your
transcription engines don't need outbound HF access in steady state.

---

## What an engine does with the loaded model

Once the model is in `/models`, the engine loads it into RAM/VRAM lazily.
For pyannote that's `Pipeline.from_pretrained(model_id, token=hf_token, revision="main")`
with the model moved to `cuda` or `mps` if available
([`engine.py:96-110`](../../engines/stt-diarize/pyannote-4.0/engine.py#L96-L110)).

For batch processing the engine pulls a `TaskRequest` from its Redis stream,
materializes the audio file from S3 to local disk (handled by the SDK base
class), runs the model, writes the output JSON back to S3, and emits
`task.completed`. See [31-pipeline-stages-explained.md](31-pipeline-stages-explained.md).

---

## Common errors

- **`HF_TOKEN environment variable is required for pyannote`** — set the env
  var and re-launch.
- **`401 Unauthorized` from HuggingFace** — your token is valid but you
  haven't accepted the model's license on the HF web UI.
- **`Disk full` mid-download** — `/data` ran out. Default EBS is 50 GB; bump
  with `data_volume_gb` in your template.
- **Model download is slow** — using HF directly. Pre-stage to S3 (above).
- **Model loads, but inference is on CPU even with a GPU available** —
  CUDA/runtime mismatch in the container. Check `nvidia-smi` works inside
  the container; check the engine's logs for `device=cpu`.

---

## See also

- [12-engine-presets-catalog.md](12-engine-presets-catalog.md) — what each preset's default model is
- [31-pipeline-stages-explained.md](31-pipeline-stages-explained.md) — what engines do once the model is loaded
- [32-diarization-vs-transcription.md](32-diarization-vs-transcription.md) — picking model combos
- [`docs/specs/MODELS.md`](../specs/MODELS.md) — engineering reference
- [`dalston/engine_sdk/model_storage.py`](../../dalston/engine_sdk/model_storage.py) — source
