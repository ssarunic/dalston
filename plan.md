# Plan: Unified Transcript Contract (`DalstonTranscriptV1`)

## Problem Statement

Every transcription runtime (Faster-Whisper, Parakeet, Voxtral, etc.) produces
its own bespoke result, then each engine manually maps into either
`TranscribeOutput` (batch) or `TranscribeResult` (realtime). This causes:

- **High boilerplate** — adding a new model means writing redundant mapping twice
- **Metadata drift** — model-specific metrics (compression_ratio, logprobs) handled inconsistently
- **Fragility** — changes to core output types ripple through every engine adapter
- **Permissive fallback parsing** — `transcript.py` uses `_try_parse_*()` wrappers that silently swallow schema violations, hiding bugs

## Comparison: Two Proposals

| Dimension | Original (DalstonTranscript + Base Engines) | Reviewer (DalstonTranscriptV1 in pipeline_types) |
|-----------|----------------------------------------------|--------------------------------------------------|
| Location | New `dalston.engine_sdk.contracts` | Extend existing `dalston.common.pipeline_types` |
| Batch + RT | Separate base engine classes per mode | Single schema, validated at runner boundaries |
| Fallbacks | Implies removal but not explicit | Explicitly remove permissive parsing |
| Testing | Not mentioned | Contract parity tests across runtimes |
| Versioning | Not mentioned | Versioned schema (`V1`) |

**Recommendation**: Merge both. Use the reviewer's pragmatic placement (keep it in
`pipeline_types.py` where all pipeline types live) and explicit versioning, but
adopt the original's idea of base engine helpers to eliminate per-engine mapping
boilerplate. Add contract parity tests as the reviewer suggests.

---

## Design

### The canonical type: `DalstonTranscriptV1`

```python
# In dalston/common/pipeline_types.py

class TranscriptWord(StrictModel):
    """Word-level timing and confidence."""
    text: str
    start: float
    end: float
    confidence: float | None = None
    alignment_method: AlignmentMethod = AlignmentMethod.UNKNOWN
    metadata: dict[str, Any] = {}          # model-specific (logprob, etc.)

class TranscriptSegment(StrictModel):
    """A contiguous speech segment."""
    start: float
    end: float
    text: str
    words: list[TranscriptWord] | None = None
    language: str | None = None            # per-segment language (multilingual)
    confidence: float | None = None
    metadata: dict[str, Any] = {}          # model-specific (compression_ratio, no_speech_prob, etc.)

class DalstonTranscriptV1(StrictModel):
    """Canonical transcript output — returned by every transcription runtime."""
    schema_version: Literal["1"] = "1"
    text: str
    segments: list[TranscriptSegment]
    language: str
    language_confidence: float | None = None
    duration: float | None = None
    timestamp_granularity: TimestampGranularity = TimestampGranularity.SEGMENT
    alignment_method: AlignmentMethod = AlignmentMethod.UNKNOWN
    runtime: str                           # e.g. "faster-whisper", "parakeet-tdt-0.6b-v3"
    warnings: list[str] = []
    metadata: dict[str, Any] = {}          # runtime-level extras
```

Key decisions:
- **`metadata: dict[str, Any]`** at segment, word, and transcript level — carries
  model-specific flavour (Whisper's `compression_ratio`, Parakeet's decoder scores)
  without polluting the schema.
- **`StrictModel` (`extra="forbid"`)** — catches schema drift at serialisation time.
- **`schema_version`** — allows non-breaking evolution; consumers can branch on version.
- **Subsumes both `TranscribeOutput` and `TranscribeResult`** — the realtime
  `TranscribeResult` dataclass is a strict subset (text + words + language + confidence).

### What stays, what goes

| Current type | Fate | Reason |
|---|---|---|
| `TranscribeOutput` | **Deprecated → alias to `DalstonTranscriptV1`** | Transition period; engines that still return it get auto-coerced. Remove after all engines migrate. |
| `TranscribeResult` (realtime) | **Replaced** | `DalstonTranscriptV1` covers all its fields. `TranscriptAssembler` updated to accept the new type. |
| `Segment` / `Word` (pipeline_types) | **Kept** for downstream stages | Align, Diarize, Merge stages still use these. The *transcribe* boundary is what we're unifying. |
| `MergedSegment` / `MergeOutput` | **Unchanged** | These are the *final* output format, not the runtime contract. |

---

## Implementation Steps

### Phase 1 — Define the canonical type and validate at boundaries

**Step 1.1: Add `DalstonTranscriptV1` to `pipeline_types.py`**
- File: `dalston/common/pipeline_types.py`
- Add `TranscriptWord`, `TranscriptSegment`, `DalstonTranscriptV1` models
- Add `TranscribeOutputPayload = DalstonTranscriptV1` alias in `contracts.py`

**Step 1.2: Add boundary validation in batch runner**
- File: `dalston/engine_sdk/runner.py`
- After `engine.process()` returns, validate that transcribe-stage output
  conforms to `DalstonTranscriptV1` (Pydantic `model_validate`).
- Log a structured warning if validation fails (don't hard-fail yet — migration period).

**Step 1.3: Add boundary validation in realtime engine wrapper**
- File: `dalston/realtime_sdk/assembler.py` (or the session handler that calls engines)
- Validate `DalstonTranscriptV1` at the point where engine results enter the assembler.

### Phase 2 — Migrate engines to return `DalstonTranscriptV1`

**Step 2.1: Create `BaseBatchTranscribeEngine` helper**
- File: `dalston/engine_sdk/base_transcribe.py` (new)
- Provides `_to_dalston_transcript()` utility that maps common fields.
- Engines override a `transcribe_audio() -> DalstonTranscriptV1` method instead of
  building `TranscribeOutput` manually.

**Step 2.2: Create `BaseRealtimeTranscribeEngine` helper**
- File: `dalston/realtime_sdk/base_transcribe.py` (new)
- Same pattern for realtime: engine returns `DalstonTranscriptV1`, base class handles
  session-relative timestamp adjustment and assembler feeding.

**Step 2.3: Migrate batch engines (one at a time)**
- `engines/stt-transcribe/faster-whisper/engine.py` — move Whisper-specific fields
  (`compression_ratio`, `no_speech_prob`, `avg_logprob`, `tokens`, `temperature`)
  into `segment.metadata`.
- `engines/stt-transcribe/parakeet-onnx/engine.py` — similar; decoder type goes into `metadata`.
- `engines/stt-transcribe/voxtral/engine.py` — similar.
- `engines/stt-transcribe/hf-asr/engine.py`, `riva/engine.py`, `vllm-asr/engine.py` — same pattern.

**Step 2.4: Migrate realtime engine**
- `engines/stt-rt/faster-whisper/engine.py` — return `DalstonTranscriptV1` instead of
  `TranscribeResult`.

### Phase 3 — Update consumers and remove permissive parsing

**Step 3.1: Update `transcript.py` assembly**
- File: `dalston/common/transcript.py`
- Replace `_try_parse_transcribe()` with direct `DalstonTranscriptV1.model_validate()`.
- Remove the `_try_parse_*()` wrappers and fallback dict-based segment extraction
  for the transcribe stage. (Align/Diarize stages keep their own typed parsing for now.)
- If validation fails, raise explicitly — don't silently fall back to raw dicts.

**Step 3.2: Update `TranscriptAssembler` (realtime)**
- File: `dalston/realtime_sdk/assembler.py`
- `add_utterance()` accepts `DalstonTranscriptV1` instead of `TranscribeResult`.
- Remove the `TranscribeResult` dataclass.

**Step 3.3: Keep protocol adapters as pure view translators**
- Files: `gateway/api/v1/openai_audio.py`, `gateway/api/v1/speech_to_text.py`,
  `gateway/services/export.py`
- These already consume the merged transcript dict — no changes needed now.
- Future: they could accept `DalstonTranscriptV1` directly for pre-merge responses,
  but that's out of scope for this change.

### Phase 4 — Contract parity tests

**Step 4.1: Schema conformance test**
- File: `tests/unit/test_transcript_contract.py` (new)
- For each engine, load a fixture output and validate it against `DalstonTranscriptV1`.
- Assert required fields present, types correct, metadata keys documented.

**Step 4.2: Round-trip parity test**
- Serialize `DalstonTranscriptV1` → JSON → deserialize → assert equality.
- Ensures no information loss through the pipeline.

**Step 4.3: Cross-runtime field coverage test**
- Table-driven test: for each runtime (faster-whisper, parakeet, voxtral, etc.),
  assert that `timestamp_granularity`, `alignment_method`, and `language` are populated.
- Assert that model-specific fields land in `metadata`, not as top-level keys.

### Phase 5 — Cleanup

**Step 5.1: Remove `TranscribeOutput` alias**
- Once all engines return `DalstonTranscriptV1`, remove the deprecated type and alias.

**Step 5.2: Remove remaining permissive fallbacks**
- Final sweep of `transcript.py` to ensure no silent dict fallbacks remain for
  the transcribe stage.

---

## Migration Safety

- **No big-bang**: Phases 1-2 are additive. Existing engines keep working because
  `TranscribeOutput` is aliased. Boundary validation logs warnings, doesn't reject.
- **Feature flag**: `DALSTON_STRICT_TRANSCRIPT_VALIDATION=true` to flip from
  warn → reject once all engines conform.
- **Rollback**: Revert the alias and validation gate; engines still produce valid
  `TranscribeOutput` during transition.

## Files Changed (Summary)

| File | Change |
|------|--------|
| `dalston/common/pipeline_types.py` | Add `DalstonTranscriptV1`, `TranscriptSegment`, `TranscriptWord` |
| `dalston/engine_sdk/contracts.py` | Alias `TranscribeOutputPayload` to new type |
| `dalston/engine_sdk/runner.py` | Add boundary validation for transcribe outputs |
| `dalston/engine_sdk/base_transcribe.py` | New — `BaseBatchTranscribeEngine` |
| `dalston/realtime_sdk/base_transcribe.py` | New — `BaseRealtimeTranscribeEngine` |
| `dalston/realtime_sdk/assembler.py` | Accept `DalstonTranscriptV1`, deprecate `TranscribeResult` |
| `engines/stt-transcribe/*/engine.py` | Return `DalstonTranscriptV1` (6 engines) |
| `engines/stt-rt/faster-whisper/engine.py` | Return `DalstonTranscriptV1` |
| `dalston/common/transcript.py` | Remove `_try_parse_transcribe()` fallback, use strict validation |
| `tests/unit/test_transcript_contract.py` | New — contract parity tests |

## Out of Scope

- Unifying Align/Diarize/PII output types (same pattern, different change)
- Runtime validation of `engine.yaml` capabilities (separate concern)
- Changing protocol adapter response formats (these stay as pure transport translators)
