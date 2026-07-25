# Model selection and registry

Dalston separates persistent model metadata from live engine availability:

- The model registry says which models exist, their download state,
  capabilities, languages, hardware metadata, and compatible `engine_id`.
- The unified engine registry says which engine instances are running and what
  they can execute now.

A model is usable only when its files are ready and a compatible execution path
is available for the requested stage/runtime mode.

## Model identity

Registry IDs are stable user-facing identifiers and may contain provider-style
names such as `Systran/faster-whisper-base`. `loaded_model_id` is the value the
framework loads. `engine_id` identifies the authored engine implementation.
These fields are not interchangeable.

Each entry has a pipeline `stage`, normally `transcribe`, `diarize`, `align`, or
`pii_detect`. A null/empty language list means multilingual only where the
registry/API description says so; consumers should not invent support from a
model name.

## HTTP API

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/v1/models` | List entries; filter by `stage`, `engine_id`, or `status` |
| `GET` | `/v1/models/{model_id}` | Get one entry |
| `PATCH` | `/v1/models/{model_id}` | Update editable metadata |
| `POST` | `/v1/models/{model_id}/pull` | Start/force a download |
| `DELETE` | `/v1/models/{model_id}` | Remove downloaded files |
| `DELETE` | `/v1/models/{model_id}?purge=true` | Remove files and registry entry |
| `POST` | `/v1/models/sync` | Reconcile registry and model storage |
| `POST` | `/v1/models/hf/resolve` | Resolve a Hugging Face model to an engine |
| `GET` | `/v1/models/hf/mappings` | Inspect Hugging Face mapping rules |
| `GET` | `/v1/engines` | List live engine instances |
| `GET` | `/v1/engines/capabilities` | Summarize live stage capabilities |

There are no model `load` or `unload` HTTP endpoints. Engine processes load
models according to their runtime manager and assigned work. A pull downloads
files; it does not create a live worker.

Model states are `not_downloaded`, `downloading`, `ready`, and `failed`.
Download responses are asynchronous where applicable; poll the model entry or
use CLI status.

Deleting a model needed by pending/processing jobs can return `409`. Without
`purge=true`, deletion preserves metadata so the model can be pulled again.

## Job selection

Native batch submission accepts:

- `model` for transcription;
- `model_diarize`;
- `model_align`;
- `model_pii_detect`.

Stage-specific IDs must belong to the matching stage. `model=auto` asks Dalston
to choose from ready, compatible models. The orchestrator records resolved
model data in task configuration so workers do not guess.

In Lite mode, the selected profile and locally available engine runtime also
constrain selection. Unsupported combinations are reported by the Lite
capabilities endpoint rather than silently distributed.

## Realtime selection

An explicit realtime model resolves to its `engine_id` and language metadata.
Automatic selection prefers the largest ready native-streaming model with a
live realtime worker, then falls back according to registry availability.
Allocation still requires a worker advertising the realtime interface.

OpenAI and ElevenLabs model names on compatibility routes are adapter contract
labels. They are not a fixed public mapping to one internal model directory;
the current registry and worker pool determine execution.

## Hugging Face routing

The resolver examines known mapping rules and repository metadata to propose a
compatible engine. Resolution does not download files by itself. Use the
returned registry/model information with the pull endpoint.

Unknown architectures can fail resolution. Add mappings in code and tests
rather than documenting a fictional fallback engine.

## CLI

Current commands are discoverable with:

```bash
dalston models --help
dalston models list
dalston models pull MODEL_ID
```

Model status is included in `models list`. Removal/purge and registry sync are
currently REST/console operations. Use each subcommand's `--help` for current
filters and options.

## Storage and cache

Compose mounts the shared model volume at `/models`. Runtime managers can keep
a local disk cache and evict entries according to
`DALSTON_MODEL_CACHE_MAX_GB`, `DALSTON_MODEL_CACHE_TTL_HOURS`, and
`DALSTON_MODEL_CACHE_SCAN_INTERVAL`.

See [How models are fetched](../guides/30-how-models-are-fetched.md).

## Performance metadata

Catalog RTF, RAM, and VRAM values are planning metadata, not admission-control
guarantees. Results vary by accelerator, driver/framework, precision, batch
size, concurrency, and audio. Published measurements must include hardware,
model, engine version/commit, corpus, concurrency, and date.
