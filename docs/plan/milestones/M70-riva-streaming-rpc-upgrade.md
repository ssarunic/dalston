# M70: Riva Streaming RPC Upgrade

| | |
|---|---|
| **Goal** | Migrate both Riva engines from `offline_recognize()` to `streaming_recognize()` with per-path `interim_results` configuration |
| **Duration** | 2–3 days |
| **Dependencies** | Riva NIM integration (existing) |
| **Primary Deliverable** | Both `stt-batch-transcribe-riva` and `stt-rt-riva` send audio incrementally; RT gains partial-result events; batch output shape is unchanged |
| **Status** | Proposed |

## Outcomes

1. Batch engine streams audio to NIM in chunks — no timeout risk on long recordings, no single
   blocking RPC per file. Output shape (word-timestamped final segments) is identical to today.
2. RT engine gains true partial results — first-word latency reduced from utterance-boundary
   detection time to acoustic onset time.
3. Both engines converge on one gRPC method (`streaming_recognize()`) with different config.
   Two containers remain; they are intentionally separate (different scaling axes, different
   response-handling logic).

## Background

Both Riva engines currently call `offline_recognize()` — a unary RPC that takes a complete audio
payload and returns a single response. This works but has two problems:

**Batch:** the entire audio file is sent as one RPC payload. Long recordings hold a connection
open for their full duration, risk gRPC deadline exhaustion, and give NIM no opportunity to
pipeline work. No progress is visible mid-transcription.

**Realtime:** the RT engine accumulates a full VAD-detected utterance before calling
`offline_recognize()`. First-word latency equals the time to detect a silence boundary, not the
time to recognise the first word.

`streaming_recognize()` fixes both problems. The audio source (file vs WebSocket stream) and
the `interim_results` flag are the only differences between the two paths:

| | Batch | Realtime |
|---|---|---|
| Audio source | file read in chunks | WebSocket audio chunks |
| `interim_results` | `False` | `True` |
| Response events | final segments only | interim partials + final |
| Downstream output | unchanged (`TranscribeOutput`) | unchanged (partial/final WebSocket events) |

## Scope

In scope:

- Replace `offline_recognize()` with `streaming_recognize()` in both engine implementations.
- Batch engine: read audio file in configurable chunks (`DALSTON_RIVA_CHUNK_MS`, default 100 ms)
  and pipe to gRPC request iterator. Collect final-only results and assemble `TranscribeOutput`.
- RT engine: remove VAD accumulation gate; pipe raw WebSocket chunks directly to gRPC request
  iterator. Forward interim events to client; use final events for word timestamps.
- Add `DALSTON_RIVA_CHUNK_MS` env var (shared by both engines via engine.yaml).

Out of scope:

- Merging batch and RT into a unified runner (keep two containers — see architecture rationale
  below).
- Changing the Riva NIM container or NGC model configuration.
- Adding new Riva model variants.

## Why Two Containers Remain

Merging into a unified runner (as originally proposed in this milestone) adds no meaningful
value here. Unlike faster-whisper/parakeet where the GPU model loaded in-process was the shared
resource that justified a single process, the NIM sidecar already is the shared singleton. The
two Dalston containers are I/O adapters with different concerns:

- **Batch** scales with Redis queue depth; restarts don't affect live sessions.
- **RT** scales with concurrent WebSocket connections; restarts drop live sessions.

Keeping them separate preserves independent scaling, deployment, and fault-isolation.

## Tactics

### T1. Batch engine — chunked streaming

```python
# engines/stt-transcribe/riva/engine.py

def _audio_chunk_iter(audio_bytes: bytes, chunk_ms: int, sample_rate: int):
    chunk_samples = (sample_rate * chunk_ms) // 1000
    chunk_bytes = chunk_samples * 2  # int16
    for offset in range(0, len(audio_bytes), chunk_bytes):
        yield riva.client.StreamingRecognizeRequest(
            audio_content=audio_bytes[offset : offset + chunk_bytes]
        )

def process(self, engine_input, ctx):
    audio_bytes = engine_input.audio_path.read_bytes()
    config = riva.client.StreamingRecognitionConfig(
        config=riva.client.RecognitionConfig(
            language_code=language,
            enable_word_time_offsets=True,
            enable_automatic_punctuation=True,
        ),
        interim_results=False,   # final segments only
    )
    responses = self._asr.streaming_recognize(
        config, self._audio_chunk_iter(audio_bytes, self._chunk_ms, sample_rate)
    )
    return self._build_output(responses, ctx)  # collects is_final=True only
```

Gate: batch contract tests — output shape identical to `offline_recognize()` baseline.

### T2. RT engine — direct chunk forwarding

```python
# engines/stt-rt/riva/engine.py

def transcribe_stream(self, audio_iter, language):
    config = riva.client.StreamingRecognitionConfig(
        config=riva.client.RecognitionConfig(
            language_code=language,
            enable_word_time_offsets=True,
            enable_automatic_punctuation=True,
        ),
        interim_results=True,   # partial events forwarded to client
    )
    for response in self._asr.streaming_recognize(config, audio_iter):
        for result in response.results:
            if result.is_final:
                yield TranscribeResult(final=True, ...)
            else:
                yield TranscribeResult(final=False, text=result.alternatives[0].transcript)
```

VAD accumulation in the `SessionHandler` is replaced by direct chunk forwarding into the gRPC
iterator. The session handler no longer waits for a silence boundary before calling the engine.

Gate: RT session lifecycle tests pass. Verify that `is_final=False` events reach the client
WebSocket as partial transcript events.

### T3. Timeout and large-file validation

- Record a >1-hour audio file test against the batch engine; confirm no gRPC deadline exceeded.
- Confirm `DALSTON_RIVA_CHUNK_MS` is honoured and adjustable without rebuild.

## Testing Matrix

- Unit: mock `streaming_recognize()` responses with mix of interim and final results; verify
  batch collects final only, RT emits both.
- Integration: batch job through Redis queue — output parity against `offline_recognize()` baseline.
- Integration: RT session — first partial event arrives before utterance end.
- Resilience: gRPC stream interrupted mid-file; verify clean error propagation.

## Success Criteria

- Batch `TranscribeOutput` word timestamps and text match the `offline_recognize()` baseline on
  the same audio.
- RT first partial event latency is measurably lower than current first-final-result latency.
- A 90-minute audio file completes without gRPC deadline error at default NIM settings.
- `make lint` and `make test` pass.

## References

- `engines/stt-transcribe/riva/engine.py` — current batch engine
- `engines/stt-rt/riva/engine.py` — current RT engine
- `docker-compose.riva.yml` — Riva overlay (unchanged by this milestone)
- Riva Python client docs: `riva.client.ASRService.streaming_recognize()`
