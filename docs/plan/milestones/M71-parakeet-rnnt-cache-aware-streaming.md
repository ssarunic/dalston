# M71: Parakeet RNNT/TDT Cache-Aware Streaming Inference

| | |
|---|---|
| **Goal** | Wire NeMo's `CacheAwareStreamingConfig` into the RT parakeet engine so RNNT and TDT variants emit tokens as audio arrives rather than after a VAD boundary |
| **Duration** | 1–2 weeks |
| **Dependencies** | M63 (ParakeetCore shared core) |
| **Primary Deliverable** | RT parakeet engine with true frame-by-frame streaming for RNNT/TDT; CTC variants retain the existing VAD-segment path |
| **Status** | Proposed |

## Outcomes

1. RNNT and TDT variants produce partial transcript events as audio is decoded,
   not after silence is detected. First-word latency drops from utterance-boundary
   detection time to acoustic onset time (~100–200 ms target).
2. CTC variants are unaffected — they continue using the VAD-accumulate-then-transcribe path,
   which is the only correct approach for CTC.
3. `engine.yaml` and variant YAMLs that already declare `streaming: true` and
   "cache-aware FastConformer encoder" are backed by actual implementation.
4. Architecture reaches parity with Riva after M70: both engine_ids deliver genuine
   streaming partials from the model, not from post-hoc segmentation.

## Background

NeMo's Parakeet RNNT and TDT models use a FastConformer encoder with optional
cache-aware inference. The cache stores encoder state across audio chunks so that
the encoder never reprocesses prior context — each new chunk is encoded
incrementally. The RNNT/TDT decoder emits tokens frame-by-frame as chunks arrive.

The current RT parakeet engine ignores this capability entirely. It uses the VAD
processor to accumulate a complete utterance, then calls `model.transcribe(full_array)` —
the same batch API used by the batch engine. The variant YAML already says:

> "Uses cache-aware FastConformer encoder with RNNT decoder for true streaming.
> RNNT is the only decoder architecture that supports streaming inference."

This milestone closes the gap between the documented intent and the implemented behaviour.

**CTC cannot stream** — CTC alignment requires the full sequence to compute the
optimal path. The VAD-segment path remains correct and unchanged for CTC variants.

## Scope

In scope:

- Extend `ParakeetCore` with a `transcribe_streaming()` method using
  `CacheAwareStreamingConfig` that yields `NeMoWordResult` events incrementally.
- Update the RT parakeet engine (`engines/stt-rt/parakeet/engine.py`) to call
  `transcribe_streaming()` for RNNT/TDT variants, bypassing VAD accumulation.
- Retain the VAD-accumulate path as the code path for CTC variants.
- Update `ParakeetOnnxCore` only if the ONNX engine_id exposes an equivalent API
  (parakeet-onnx uses CTC; likely out of scope).
- Add `DALSTON_RNNT_CHUNK_MS` env var (default 160 ms — one FastConformer chunk).

Out of scope:

- Streaming inference for the batch engine — batch files benefit from chunked
  network transfer (M70 Riva concern), not from incremental decoding. The full
  audio is available up front; returning only final results is correct.
- Cache-aware streaming for parakeet-onnx (CTC architecture; not applicable).
- Changes to the vllm-asr or faster-whisper engines.

## Key NeMo APIs

```python
from nemo.collections.asr.models import EncDecRNNTBPEModel
from nemo.collections.asr.parts.utils.streaming_utils import CacheAwareStreamingConfig

cfg = CacheAwareStreamingConfig(
    chunk_size=chunk_frames,          # encoder frames per chunk (~160ms)
    left_chunks=2,                    # left context chunks kept in cache
    max_symbols_per_step=10,          # RNNT decoder steps per chunk
    return_hypotheses=True,
)

# Streaming transcription — yields one hypothesis per chunk
for hypothesis in model.transcribe_streaming(audio_chunk_iter, cfg):
    for token in hypothesis.timestep:
        yield NeMoWordResult(word=token.word, start=token.start, end=token.end)
```

The `audio_chunk_iter` is a generator of `np.ndarray` chunks arriving from the
WebSocket — no VAD accumulation required.

## Tactics

### T1. `ParakeetCore.transcribe_streaming()` (dark launch)

Add to `dalston/engine_sdk/cores/parakeet_core.py`:

```python
def transcribe_streaming(
    self,
    audio_iter: Iterator[np.ndarray],
    language: str,
    chunk_ms: int = 160,
) -> Iterator[NeMoWordResult]:
    """Yield word results incrementally as audio chunks arrive.

    Only valid for RNNT and TDT model variants. Raises ``RuntimeError``
    if called on a CTC model.
    """
```

No callers changed in this commit. Unit tests mock `model.transcribe_streaming`.

Gate: unit tests pass; existing `transcribe()` path unchanged.

### T2. RT engine — decoder-aware dispatch

Update `engines/stt-rt/parakeet/engine.py` to select the inference path based on
decoder architecture:

```python
def transcribe(self, audio_chunk_iter, language, model_variant):
    decoder_type = self._core.decoder_type(model_variant)
    if decoder_type in ("rnnt", "tdt"):
        yield from self._core.transcribe_streaming(audio_chunk_iter, language)
    else:
        # Existing path: VAD accumulation → model.transcribe(full_array)
        full_audio = np.concatenate(list(audio_chunk_iter))
        yield self._core.transcribe(full_audio, language)
```

The `SessionHandler` in `realtime_sdk` receives yielded `NeMoWordResult` events
and forwards interim ones as partial transcript events to the client WebSocket.

Gate: RT session contract tests pass for both dispatch paths.

### T3. VAD bypass for streaming path

When `transcribe_streaming()` is active, the `SessionHandler` skips VAD
accumulation and passes audio chunks directly to the engine as they arrive from
the WebSocket. VAD may still run in parallel for endpoint detection (to know when
to flush the final hypothesis and reset encoder cache), but it no longer gates
inference.

Gate: integration test — first partial event from an RNNT session arrives within
300 ms of the first word spoken (measured against a reference recording with
known onset time).

### T4. Observability

- Log `decoder_type` and `streaming_path` on session start for observability.
- Metric: `rt_first_partial_latency_ms` — time from session start to first partial
  event. Separate labels for `rnnt_streaming` vs `vad_segment` paths.

## Testing Matrix

- Unit: `transcribe_streaming()` with mock chunk iterator; verify correct word
  ordering and timestamp continuity across chunk boundaries.
- Unit: CTC variant raises `RuntimeError` if `transcribe_streaming()` called directly.
- Integration: RNNT RT session — partial events arrive before utterance end.
- Integration: CTC RT session — unchanged behaviour (final-only, VAD-gated).
- Regression: batch parakeet engine unaffected (uses `transcribe()` only).
- Latency: first partial event ≤300 ms from acoustic onset on reference recording.

## Success Criteria

- RNNT and TDT RT sessions emit `is_final=False` partial events before silence.
- CTC RT sessions are behaviorally identical to today.
- `make lint` and `make test` pass.
- First-word latency for RNNT sessions measurably lower than VAD-segment baseline.

## Relationship to M70

M70 (Riva streaming RPC upgrade) and M71 address the same architectural gap from
different sides. After both milestones:

| Runtime | Streaming mechanism | Partials |
|---|---|---|
| parakeet-rnnt / tdt | NeMo `CacheAwareStreamingConfig` | Yes (M71) |
| parakeet-ctc | VAD segment → `model.transcribe()` | No (CTC limitation) |
| Riva RNNT | `streaming_recognize(interim_results=True)` | Yes (M70) |

faster-whisper is not autoregressive; its partial-result story (per-segment VAD)
remains unchanged and is architecturally correct for that model family.

## References

- `dalston/engine_sdk/cores/parakeet_core.py` — `ParakeetCore` (extend here)
- `engines/stt-rt/parakeet/engine.py` — RT adapter (update dispatch)
- `engines/stt-rt/parakeet/engine.yaml` — already declares cache-aware streaming intent
- `engines/stt-rt/parakeet/variants/rnnt-0.6b.yaml` — "RNNT is the only decoder architecture that supports streaming inference"
- NeMo docs: `CacheAwareStreamingConfig`, `EncDecRNNTBPEModel.transcribe_streaming()`
- `docs/plan/milestones/M70-riva-streaming-rpc-upgrade.md`
- `docs/plan/milestones/M63-engine-unification-incremental.md`
