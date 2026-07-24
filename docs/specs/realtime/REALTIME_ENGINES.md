# Realtime engines

Realtime engines implement Dalston's internal worker WebSocket protocol through
`dalston.realtime_sdk`. Provider compatibility belongs in gateway adapters, not
inside an engine.

## Registration

At startup, a worker registers and heartbeats through the unified engine
registry. Its record includes:

- unique instance and internal WebSocket endpoint;
- `engine_id` and realtime interface support;
- loaded model IDs and supported languages;
- session capacity and active-session count;
- runtime/capability metadata.

The embedded `SessionCoordinator` uses this data for allocation. A model
registry entry's `engine_id` must match a live worker for specific-model
routing.

## Session protocol

After the gateway connects, it sends a session configuration containing the
session ID, model/language choices, encoding and sample rate, VAD settings,
interim/timestamp requests, and optional vocabulary. Audio then arrives as
binary frames; control JSON commits or ends input.

Workers emit:

- session begin/end;
- speech start/end;
- partial and final transcript events;
- warning and error events;
- timing/lag statistics used by the shared proxy.

Engines may provide word timestamps, confidence/log probabilities, character
tokens, vocabulary forcing, and native streaming. They must advertise only
capabilities they actually implement. Gateway adapters omit unavailable
optional fields or surface a warning.

## Streaming and chunked models

Dalston supports genuinely streaming engines and chunked/windowed adapters.
Both use the realtime worker interface, but their latency and revision behavior
differ:

- native streaming models maintain incremental model state;
- chunked models use VAD/windowing and can revise provisional output;
- final transcript events define stable utterance boundaries.

Do not infer a public compatibility model mapping from an engine directory
name. The gateway resolves compatibility labels through model selection and
live engine availability.

## VAD, backpressure, and capacity

The realtime SDK owns buffering, VAD boundaries, maximum utterance handling,
and lag measurement. Important defaults can be overridden with:

- `DALSTON_REALTIME_MIN_SILENCE_DURATION_MS`
- `DALSTON_REALTIME_MAX_UTTERANCE_DURATION`
- the `DALSTON_RT_*` lag/backpressure variables documented in
  [Latency budget and backpressure](./LATENCY_BUDGET_BACKPRESSURE.md)

Each worker declares a maximum session capacity. The SDK refuses excess direct
connections and the gateway coordinator prevents allocation beyond the
advertised capacity.

## Model lifecycle

Engines load model files from the shared `/models` volume or their configured
model source. Model registry entries provide `loaded_model_id` and `engine_id`;
runtime model managers translate those values into framework-specific loading.

Disk-cache eviction is controlled by `DALSTON_MODEL_CACHE_MAX_GB`,
`DALSTON_MODEL_CACHE_TTL_HOURS`, and
`DALSTON_MODEL_CACHE_SCAN_INTERVAL`. See
[How models are fetched](../../guides/30-how-models-are-fetched.md).

## Implementing an engine

Use the current realtime SDK base classes and an existing authored realtime
engine as the template. The engine must:

1. validate session configuration and audio format;
2. register only after model initialization succeeds;
3. heartbeat and update active-session counts;
4. release per-session resources on every close path;
5. enforce lag and utterance bounds;
6. emit schema-valid events;
7. shut down by draining or terminating sessions predictably.

Unit-test the session handler, then test through all gateway adapters whose
features the engine advertises. Performance results must identify hardware,
model, engine commit/version, audio corpus, concurrency, and date.

## Deployment

Realtime engines are ordinary engine containers; there is no separate
session-router container. Their internal WebSocket endpoints must be reachable
from the gateway network but do not need to be public. Use the current Compose
service/profile definitions or the engine deployment scripts instead of the
historical snippets formerly embedded in this document.
