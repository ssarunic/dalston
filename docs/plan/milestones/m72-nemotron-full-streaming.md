# M72: Nemotron Full Streaming

| | |
|---|---|
| **Goal** | Route `nemotron-speech-streaming-en-0.6b` through `BatchedFrameASRRNNT(stateful_decoding=True)` so it emits partial transcript events chunk-by-chunk during speech, not only at VAD utterance boundaries |
| **Duration** | 1 week |
| **Dependencies** | M71 (RNNT/TDT VAD-accumulate streaming) |
| **Primary Deliverable** | Nemotron sessions deliver partial tokens during speech with ~160ms chunk latency; offline Parakeet RNNT/TDT unchanged |
| **Status** | Proposed |

## Outcomes

1. `nemotron-speech-streaming-en-0.6b` sessions emit `is_final=False` partial transcript events
   as audio arrives — one per decoded word — without waiting for VAD `speech_end`. First-word
   latency drops from utterance-boundary detection time to acoustic onset time (~160–320ms target).

2. Offline Parakeet RNNT and TDT variants (`parakeet-rnnt-*`, `parakeet-tdt-*`) are unaffected.
   They continue through the VAD-accumulate path because their encoders were trained with full
   future context and produce degraded output on partial audio.

3. CTC variants are unaffected. The VAD-segment path remains correct for CTC.

4. A new `is_cache_aware_streaming()` predicate in `NeMoModelManager` and `NemoInference` provides
   a stable extension point for future models trained with limited right context.

5. `DALSTON_RNNT_BUFFER_SECS` env var (already documented in `rt_engine.py` but not wired) is
   implemented and controls the `BatchedFrameASRRNNT` rolling buffer size.

## Background

M71 added RNNT/TDT streaming support but chose the VAD-accumulate path for all NeMo models.
The docstring explains why:

> "Offline RNNT/TDT models require the full audio context, so transcribe_streaming() buffers the
> entire utterance and emits words only after stream exhaustion. Exposing that as a streaming decode
> fn breaks utterance boundary detection: VAD speech_end flushes find an empty buffer because the
> sentinel hasn't been pushed yet."

This was correct for `parakeet-rnnt-*` and `parakeet-tdt-*`, which were trained offline.

**Nemotron is different.** `nvidia/nemotron-speech-streaming-en-0.6b` (released Jan 2026) was
purpose-built for cache-aware streaming inference with a limited right context. It is designed to
be fed audio in fixed chunks and to emit tokens incrementally as each chunk is processed. Running
it through the batch `model.transcribe()` path ignores this capability entirely and imposes a full
utterance latency where none is needed.

NeMo provides `BatchedFrameASRRNNT(stateful_decoding=True)` as the standard API for this model:

- `stateful_decoding=True` carries the RNNT decoder's hidden states and beam hypothesis across
  chunk boundaries.
- The rolling audio buffer (`total_buffer`) handles left context without re-encoding prior audio.
- Partial hypotheses are emitted after each chunk; `_emit_new_words()` diffs consecutive hypotheses
  to extract newly decoded tokens.

The session handler's per-chunk streaming loop (`_streaming_decode_loop`, `_streaming_chunk_queue`)
already exists from M71 but is never activated for NeMo engines because `get_streaming_decode_fn`
returns `None`. This milestone wires Nemotron into that existing path.

## Strategy

**Single predicate, two paths.** Rather than changing the global RNNT streaming logic, introduce
`is_cache_aware_streaming(model_id)` as a narrow flag that is `True` only for models specifically
trained with limited right context. Everything else falls through to existing behaviour.

**`BatchedFrameASRRNNT` wraps the model.** The offline `model.transcribe()` batch call is replaced
by a stateful `BatchedFrameASRRNNT` instance that processes the chunk iterator directly. Word
emissions are derived by diffing consecutive cumulative hypotheses via the existing
`_emit_new_words()` helper.

**`get_streaming_decode_fn` activates the session handler loop.** Returning `self.transcribe_streaming`
(a bound method with the exact signature the session handler expects) is all that is needed to
switch a Nemotron session from VAD-accumulate to per-chunk streaming. No changes to the session
handler are required.

**Utterance boundaries remain VAD-driven.** The session handler runs VAD in parallel with the
decode loop. On `speech_end`, `_flush_streaming_final()` assembles all accumulated partial words
into the final `TranscriptFinalMessage`. This is unchanged.

## Plan

### Step 1 — `NeMoModelManager.is_cache_aware_streaming()` (dark launch)

Add to `dalston/engine_sdk/managers/nemo.py`:

```python
CACHE_AWARE_STREAMING_MODELS = frozenset({"nemotron-streaming-rnnt-0.6b"})

def is_cache_aware_streaming(self, model_id: str) -> bool:
    """Return True if model was trained for per-chunk cache-aware streaming.

    Only models with limited right context (e.g. nemotron-speech-streaming-en-0.6b)
    should return True. Offline models (parakeet-rnnt-*, parakeet-tdt-*) return False
    even though they are RNNT/TDT architecture.
    """
    return model_id in self.CACHE_AWARE_STREAMING_MODELS
```

No callers changed. Unit tests verify the predicate.

Gate: `is_cache_aware_streaming("nemotron-streaming-rnnt-0.6b")` → `True`;
all other model IDs → `False`.

### Step 2 — `NemoInference._run_batched_frame_asr()`

Add to `dalston/engine_sdk/inference/nemo_inference.py`:

- `is_cache_aware_streaming(model_id)` passthrough to manager.
- `buffer_secs: float = 4.0` parameter to `transcribe_streaming`.
- Branch inside `transcribe_streaming`: if `is_cache_aware_streaming` → `_run_batched_frame_asr`;
  otherwise → existing `_run_streaming_inference`.
- `_run_batched_frame_asr(model, audio_iter, chunk_ms, buffer_secs)`:
  - Creates `BatchedFrameASRRNNT(asr_model=model, frame_len=chunk_ms/1000, total_buffer=buffer_secs,
    batch_size=1, max_steps_per_step=10, stateful_decoding=True)`.
  - Derives `model_stride_in_secs` from model config (falls back to `0.08` for FastConformer).
  - For each chunk: calls `read_audio_file`, `transcribe`, diffs via `_emit_new_words`, yields
    `NeMoWordResult`.

Gate: unit tests with mocked `BatchedFrameASRRNNT` pass.

### Step 3 — `rt_engine.py` wiring

Update `engines/stt-transcribe/nemo/rt_engine.py`:

- Read `DALSTON_RNNT_BUFFER_SECS` env var in `__init__` (default `4.0`).
- Pass `buffer_secs=self._rnnt_buffer_secs` in `transcribe_streaming` call.
- Override `get_streaming_decode_fn`: return `self.transcribe_streaming` when model is
  cache-aware, else `None`.

Gate: `get_streaming_decode_fn("nemotron-streaming-rnnt-0.6b")` returns a callable;
`get_streaming_decode_fn("parakeet-rnnt-1.1b")` returns `None`.

## Tests

### Unit

- `TestCacheAwareStreamingFlag` — predicate returns `True` only for Nemotron; `False` for all
  offline RNNT/TDT and CTC models.
- `TestNemoInferenceRouting` — `transcribe_streaming` routes to `_run_batched_frame_asr` for
  Nemotron and `_run_streaming_inference` for offline RNNT.
- `TestRunBatchedFrameASR` — with mocked `BatchedFrameASRRNNT`:
  - Yields `NeMoWordResult` per new word on each chunk.
  - Emits nothing when hypothesis unchanged.
  - Empty audio iterator produces no output.
  - `reset()` called exactly once.
  - `model_stride_in_secs` read from model config; falls back to `0.08`.
- `TestRTEngineNemotronStreamingDecodeRoute`:
  - `get_streaming_decode_fn("nemotron-streaming-rnnt-0.6b")` returns non-`None` callable.
  - `get_streaming_decode_fn("parakeet-rnnt-1.1b")` returns `None` (offline RNNT unchanged).
  - `get_streaming_decode_fn("parakeet-ctc-0.6b")` returns `None`.
  - `DALSTON_RNNT_BUFFER_SECS` env var is read and threaded through.
- `TestNemotronNormalizeModelId` — short alias `nemotron-0.6b` and full HuggingFace path both
  normalize to `nemotron-streaming-rnnt-0.6b`.

### Regression

- All existing `test_parakeet_rnnt_streaming.py` tests pass unchanged (offline RNNT path
  unaffected).
- `test_parakeet_rt_contract.py` passes (transcribe_v1 path unaffected).

## Success Criteria

- Nemotron sessions activate the session handler's per-chunk streaming loop (not VAD-accumulate).
- Partial events arrive during speech, not only at utterance end.
- Offline Parakeet RNNT/TDT and CTC sessions are behaviourally identical to M71.
- `make lint` and `make test` pass.

## Model Routing Summary (post-M72)

| Model | Architecture | Trained for streaming | `get_streaming_decode_fn` | Inference path |
|---|---|---|---|---|
| `parakeet-rnnt-0.6b` | RNNT | No (offline) | `None` | VAD-accumulate → batch `transcribe()` |
| `parakeet-rnnt-1.1b` | RNNT | No (offline) | `None` | VAD-accumulate → batch `transcribe()` |
| `parakeet-tdt-0.6b-v3` | TDT | No (offline) | `None` | VAD-accumulate → batch `transcribe()` |
| `parakeet-tdt-1.1b` | TDT | No (offline) | `None` | VAD-accumulate → batch `transcribe()` |
| `parakeet-ctc-0.6b` | CTC | No | `None` | VAD-accumulate → batch `transcribe()` |
| `parakeet-ctc-1.1b` | CTC | No | `None` | VAD-accumulate → batch `transcribe()` |
| `nemotron-streaming-rnnt-0.6b` | RNNT | **Yes** | `transcribe_streaming` | Per-chunk `BatchedFrameASRRNNT` |

## References

- `dalston/engine_sdk/managers/nemo.py` — add `CACHE_AWARE_STREAMING_MODELS`, `is_cache_aware_streaming()`
- `dalston/engine_sdk/inference/nemo_inference.py` — add `_run_batched_frame_asr()`, branch
- `engines/stt-transcribe/nemo/rt_engine.py` — wire `get_streaming_decode_fn`, `DALSTON_RNNT_BUFFER_SECS`
- NeMo `BatchedFrameASRRNNT`: `nemo.collections.asr.parts.utils.streaming_utils`
- NVIDIA model card: `nvidia/nemotron-speech-streaming-en-0.6b`
- `docs/plan/milestones/m71-parakeet-rnnt-cache-aware-streaming.md`
