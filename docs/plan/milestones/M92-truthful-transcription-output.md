# M92: Truthful Transcription Output (Per-Channel + NeMo Fixes)

|                    |                                                              |
| ------------------ | ------------------------------------------------------------ |
| **Goal**           | Every field in a transcription response is either real or absent — no hardcoded confidences, no post-processed metadata masquerading as original-file properties, no silently dropped speech or swallowed warnings |
| **Duration**       | 4–6 days                                                     |
| **Dependencies**   | M86 (Shared VAD Chunking)                                    |
| **Deliverable**    | NeMo language/confidence honesty, original-media metadata in per-channel responses, engine-warning propagation, word-timestamp root-cause fix, speech-coverage warnings, repro test fixture |
| **Status**         | Not Started                                                  |

## User Story

> *"As an API consumer building a training-data pipeline, I want response metadata and confidence fields to reflect what actually happened during transcription, so that I can filter and segment transcripts without being misled by fabricated values or silent data loss."*

---

## Origin

A 56 s Croatian stereo 8 kHz support call, transcribed with `nvidia/parakeet-tdt-0.6b-v3` (routed to the NeMo engine per `models/parakeet-tdt-0.6b-v3.yaml:6` `engine_id: nemo`), per-channel diarization, `language=hr`, vocabulary `"Bizzon, AgentCASH"`. External analysis of the response vs the raw WAV surfaced eight anomalies. Two code-investigation passes (2026-07-23, incl. independent review) confirmed the findings below.

| # | Finding | Status | Step |
|---|---------|--------|------|
| F1 | `language=hr` never passed to NeMo inference; channel auto-detected as Polish. (The selector *does* use language as a hard model-compatibility filter — it just stops there.) | Confirmed in code | 92.3 |
| F2 | `language_confidence` fabricated (`1.0`/`0.5`) by the NeMo **and** ONNX engines; `TranscriptMetadata` non-nullable with `default=1.0`; ElevenLabs compat fabricates `0.0` | Confirmed in code | 92.4 |
| F3 | `audio_channels: 1`, `sample_rate: 16000` on a stereo 8 kHz file — split-channel-file properties reported as original | Confirmed in code | 92.5 |
| F4 | `words` empty despite `timestamps=True` being requested from NeMo | Needs runtime repro (92.1) | 92.6 |
| F5 | No confidence signal of any kind from NeMo (`avg_logprob`/`no_speech_prob` null, word confidence never populated) — absent enhancement, not a defect | Confirmed in code | 92.8 |
| F6 | ~11.5 s of the loudest speech silently dropped, no warning. **The drop happened inside NeMo's direct-path decode** — a 56 s file never touches any VAD (NeMo chunking cap 1500 s). The ONNX always-VAD silent-drop is a real *latent* bug fixed alongside, but cannot explain this job. | Incident mechanism needs 92.1; latent ONNX/chunker drops confirmed in code | 92.7 |
| F7 | Segment end 25.92 s vs actual speech end ~3.5 s — end times not clamped to recognized content (NeMo: verbatim hypothesis spans; ONNX: VAD region boundaries). Clamping needs words, so for NeMo it depends on F4's fix. | Confirmed in code | 92.7 |
| F8 | Vocabulary boosting failure swallowed by blanket `except Exception`; temp vocab file **leaks on every failure path**; partial config mutation before first fallible access | Confirmed in code; deployment no-op unconfirmed (92.1) | 92.2 |
| F9 | Engine `Transcript.warnings` never propagated by **either** assembly path (per-channel *and* standard); `_select_segments` also drops align warnings on the *success* branch | Confirmed in code | 92.2 |

Not Dalston bugs, out of scope: the audio itself carries almost no energy above 2 kHz (recording-path low-pass destroys Croatian sibilant distinctions — `Matković → Matkoć`, hr↔pl LID confusion); residual Croatian text errors are model-level. The "v4" model in the original report was a v3 misread.

### Confirmed code evidence

- **F1**: `dalston/engine_sdk/inference/nemo_inference.py:234-239` — `model.transcribe(prepared, batch_size=…, return_hypotheses=True, timestamps=True)`; no language argument. `engines/stt-transcribe/nemo/batch_engine.py:414-420` uses `params.language` only to label the output. Upstream, `orchestrator/engine_selector.py` enforces language↔model compatibility both for auto-selection (hard filter, `:281`) and for explicit model requests (raises `NoCapableEngineError`, `:533-544`) — so a compatible request reaches the engine and is then ignored; an incompatible one never reaches it. Parakeet v3 has no language-forcing API at all (NVIDIA model card: automatic LID, no language argument to `transcribe`).
- **F2**: `batch_engine.py:420` and `engines/stt-transcribe/onnx/batch_engine.py:181` — `language_confidence=1.0 if language != "auto" else 0.5`. The canonical `Transcript.language_confidence` is already nullable (`pipeline_types.py:713-715`); the remaining non-nullable layer is `TranscriptMetadata.language_confidence` (`pipeline_types.py:844`, `default=1.0`) plus the `else 1.0` backstops in `common/transcript.py:226, 241, 471`. The ElevenLabs compat layer fabricates a third value: `_resolve_language_fields` maps missing confidence to `0.0` (`gateway/api/v1/speech_to_text.py:655-660`).
- **F3**: `engines/stt-prepare/audio-prepare/engine.py:220,244-246` re-probes the resampled mono channel file; `dalston/common/transcript.py:438-460` (`_extract_audio_metadata`) reads `channel_files[0]`, and at `:329` `audio_channels or channel_count` never falls through because 1 is truthy. The original media is already available twice: on `JobModel` (`db/models.py:130-132` — `audio_duration`, `audio_sample_rate`, `audio_channels`) and as a complete `AudioMedia` in the prepare task's request payload (`orchestrator/scheduler.py:520`). Note: for `per_channel` the live assembler is `assemble_per_channel_transcript` in `common/transcript.py` — `final-merger`'s `_merge_per_channel` is dead code (no merge task in the per-channel DAG, `orchestrator/dag.py:232-233`).
- **F4**: `nemo_inference.py:643-673` — two candidate causes, undecided statically: (a) NeMo returned segment timestamps but no word timestamps for this decode (the bracket filter would have *passed* words inside the observed inflated segment, so plain filter loss doesn't fit — unless (b) the word and segment timestamp representations disagree in scale/offset, in which case the filter drops everything). Prime suspect for (a): `_configure_vocabulary_boosting` (`batch_engine.py:220-228`) calls `change_decoding_strategy` immediately before `transcribe(timestamps=True)`, and the user *did* supply vocabulary. 92.1 logs raw keys, counts, **and sample values** to decide.
- **F5**: `nemo_inference.py:647-654` builds `NeMoWordResult` without confidence; `batch_engine.py:392-399` passes no extras into `build_segment`. NeMo supports `preserve_word_confidence` on the decoding config, so 92.8 is feasible.
- **F6/F7**: For this job (NeMo, 56 s): direct path — `base_transcribe.py:113-122` chunks only above the 1500 s cap, so no VAD ran; the dropped 34–56 s speech and the 0.24→25.92 s span are properties of the NeMo hypothesis itself (decode under local attention on degraded audio). Latent, engine-independent versions of the same silent-drop class: ONNX VADs *every* file (`onnx_inference.py:187-191`) with segment bounds = Silero region boundaries (`:429-430, 453-454`) and undetected speech never decoded; the M86 `VadChunker` returns `[]` on no speech (`engine_sdk/vad.py:375-377`) and `base_transcribe.py:320-326` then emits an empty transcript with no warning. ONNX duration telemetry records the **max VAD segment end** (`onnx_inference.py:310`) — it includes gaps between detected regions but excludes everything after the last detected region, so trailing drops are invisible in RTF metrics.
- **F8**: `batch_engine.py:192-249` — blanket `except Exception` with no stack logging; the temp vocab file is created (`:202-209`) *before* the fallible config access, and the `except` path returns `None` without unlinking, while the caller's `finally` (`:366-372`) only deletes when the returned path is non-null → **file leak on every failure**. `decoding_cfg.strategy = "greedy_batch"` (`:221`) also mutates shared config before the first access that can raise (`:222`), and `_reset_decoding_strategy` is skipped on the failure path.
- **F9**: `common/transcript.py:512-539` (`_select_segments`) propagates warnings **only** from a *skipped* align response. `transcript.warnings` from the transcribe engine is read nowhere in either `assemble_per_channel_transcript` or the standard assembly path, and a *successful* align response's warnings are dropped too. The F8 warning appended at `batch_engine.py:409-412` therefore never reaches `metadata.pipeline_warnings`.

---

## Steps

Ordered so that diagnosis (92.1) lands first, then warning/honesty fixes that are pure plumbing, then behavior fixes that depend on 92.1's findings.

### 92.1: Repro fixture + runtime diagnosis

**Files modified:**

- `tests/fixtures/audio/stereo_8k_narrowband.wav` *(new)* — synthetic ~30 s stereo 8 kHz fixture: distinct speech on each channel with non-overlapping turns, one channel including turns separated by long line-noise gaps (generate with ffmpeg: TTS or tone-modulated noise low-passed at 2 kHz to mimic the failing call's spectrum)
- `dalston/engine_sdk/inference/nemo_inference.py` — DEBUG logging in `_parse_hypothesis`: `list(ts_dict.keys())`, `len(word_timestamps)`, `len(segment_timestamps)`, **and the first/last few raw word and segment entries** (start/end values, to catch scale/offset disagreement between the two representations)

**Deliverables:**

Answer the open questions before touching behavior:

1. **F4 root cause.** Run the NeMo engine on the fixture four ways: {vocabulary on, off} × {tdt-0.6b-v3, tdt-1.1b}. The raw-value logging distinguishes hypothesis-has-no-words (→ fix in the vocab/decoding interaction) from representation-mismatch-drops-words (→ fix the bracket filter, 92.6.2 alone suffices).
2. **F6 incident mechanism.** Reproduce against the archived support-call WAV on the NeMo engine (which served the job — settled by the model catalog, not open). Log the full hypothesis segment spans: does NeMo emit *anything* for 34–56 s (mistranscription → coverage warning is the right fix) or literally nothing (decode truncation → investigate local-attention config, `_enable_local_attention`)?
3. **F8 deployment reality.** Inside the nemo container: `python3 -c` asserting `model.cfg.decoding.greedy.boosting_tree` exists for a loaded TDT model. If absent, the shipped image silently no-ops all vocabulary requests and 92.2's startup capability check is mandatory.

---

### 92.2: Warning propagation + vocabulary-boosting robustness (F8, F9)

**Files modified:**

- `dalston/common/transcript.py` — propagate engine warnings in **both** assembly paths
- `engines/stt-transcribe/nemo/batch_engine.py` — loud + leak-free boosting failure; startup capability check

**Deliverables:**

1. Extend `pipeline_warnings` with `transcript.warnings` in `assemble_per_channel_transcript` (prefix `ch{n}:`) **and** in the standard assembly path; also propagate a successful `align_response.warnings` in `_select_segments` (currently only the skipped branch does).
2. In `_configure_vocabulary_boosting`:
   - Keep the broad `except Exception` (the config surface can raise arbitrary OmegaConf/NeMo runtime errors — narrowing it just reintroduces silent crash paths elsewhere), but make it loud: `logger.exception` (stack, not `str(e)`).
   - **Unlink the temp vocab file on the failure path** — cleanup must happen on every exit, not only when a path is returned.
   - Build the config change on a copy (`copy.deepcopy(model.cfg.decoding)`); assign + `change_decoding_strategy` only after all key accesses succeed, so a failure leaves the model's decoding config untouched (currently `strategy` is mutated before the first fallible access).
   - The existing user-facing warning (`batch_engine.py:409-412`) now actually reaches the response via (1).
3. At engine startup (first model load), probe for `boosting_tree` support once and log `vocabulary_boosting_supported=true/false`; if unsupported, every job with vocabulary gets the warning without attempting configuration.

---

### 92.3: Honest language handling (F1)

**Files modified:**

- `engines/stt-transcribe/nemo/batch_engine.py` — language warning + provenance
- `dalston/common/pipeline_types.py` — `language_source` field
- `dalston/common/transcript.py` — propagate provenance to metadata

**Deliverables:**

Parakeet cannot force a decode language (no such argument exists; v3 auto-detects per utterance), and the selector already guarantees the requested language is *catalog-compatible* with the model before the job runs — explicit incompatible requests are rejected with `NoCapableEngineError`, auto-selection hard-filters. What's missing is honesty about what happened *after* selection:

1. Add `supports_language_forcing: bool = True` to `EngineCapabilities`; NeMo and ONNX engines set `False` (faster-whisper keeps `True`).
2. Add `language_source: Literal["requested", "detected"] | None` to `Transcript` and `TranscriptMetadata`. Engines that force or detect language set `"detected"` (faster-whisper); engines that merely echo the request set `"requested"` (NeMo, ONNX). This keeps the `language` field populated (the ElevenLabs compat layer requires a code) while making its provenance explicit instead of misrepresenting an echo as model output.
3. When `params.language` is set (≠ auto) on an engine with `supports_language_forcing=False`, append a transcript warning: `"Engine 'nemo' cannot force language 'hr'; model auto-detects language per utterance"`. Reaches the response via 92.2.
4. Fix the stale "English-only" docstrings in `batch_engine.py` (module, class, `get_capabilities`) — v3 is 25-language multilingual.

**Not needed:** per-engine validation of unsupported language codes — the selector already rejects those before dispatch (both request paths). **Non-goal:** preferring `supports_language_forcing` engines in selection — see Non-Goals.

---

### 92.4: Nullable language confidence (F2)

**Files modified:**

- `dalston/common/pipeline_types.py` — `TranscriptMetadata.language_confidence: float | None = None` (the `Transcript` field is already nullable); **bump `PIPELINE_SCHEMA_VERSION`** per `docs/guides/TYPED_ENGINE_CONTRACTS.md`
- `engines/stt-transcribe/nemo/batch_engine.py` **and** `engines/stt-transcribe/onnx/batch_engine.py` — pass `language_confidence=None` (both currently fabricate `1.0`/`0.5`)
- `dalston/common/transcript.py` — remove the `else 1.0` backstops at `:226, :241, :471`; `round()` only when not None
- `dalston/gateway/api/v1/speech_to_text.py` — `_resolve_language_fields` documentation
- `sdk/`, `cli/` — audit for float assumptions

**Deliverables:**

`language_confidence` is emitted only by engines that compute it (faster-whisper's LID probability). Everywhere else it is `null`. No compat shim — the response contract for NeMo/ONNX jobs changes from fake `1.0` to `null`, per the no-backward-compat convention.

Boundary cases decided here, not left open:

- **ElevenLabs compat**: the ElevenLabs schema requires `language_probability: float`, so the compat layer cannot emit null. Keep the `0.0` mapping but document it in `_resolve_language_fields` as a compat-contract concession (`0.0` = "no confidence available"), so the fabrication is at least labeled and confined to the compat surface. The native API is the truthful one.
- **Web UI**: `TaskDetail.tsx` already renders confidence conditionally (typed `number | undefined`) — omission is the existing behavior; no UI change required, just verify.

---

### 92.5: Original media metadata in per-channel responses (F3)

**Files modified:**

- `dalston/common/pipeline_types.py` — add `source_media: AudioMedia | None = None` to `PreparationResponse`; covered by the same `PIPELINE_SCHEMA_VERSION` bump as 92.4
- `engines/stt-prepare/audio-prepare/engine.py` — populate `source_media` in both split and non-split paths
- `dalston/common/transcript.py` — `_extract_audio_metadata` prefers `source_media`; fallback: `channel_count` overrides a mono channel-file probe when `split_channels` is true

**Deliverables:**

`metadata.audio_channels` / `sample_rate` / `audio_duration` report the **original uploaded file** (2 / 8000 for the failing call), matching the schema's documented intent (`pipeline_types.py:841` "Original audio channels").

Implementation detail that matters: `AudioMedia` requires `artifact_id` and `format`, and `_probe_audio()` returns `codec_name` — so `source_media` is **not** built from the probe alone. The prepare task's request payload already carries a complete original-media `AudioMedia` (built by the scheduler from the gateway probe, `orchestrator/scheduler.py:520`); audio-prepare echoes that object into `source_media`, using its own ffprobe of the source only to fill gaps (e.g. when the gateway probe returned no duration). The same data also exists on `JobModel` (`audio_duration`/`audio_sample_rate`/`audio_channels`) as a belt-and-braces fallback the assembler could read — but the artifact-payload route keeps assembly free of DB access.

Mixed-version behavior, stated precisely: `PreparationResponse` is `extra="forbid"`, but downstream consumers read stage outputs through `_get_typed_output()`, which swallows validation errors and returns `None` — so an old consumer seeing the new field degrades silently rather than erroring. That silent degradation is exactly why the schema-version bump and the standard deploy order (engines first, orchestrator second, per `TYPED_ENGINE_CONTRACTS.md`) are required, not optional.

Unit test: stereo 8 kHz fixture through prepare → assemble asserts `(2, 8000)`.

---

### 92.6: Word-timestamp integrity (F4)

**Files modified:**

- `dalston/engine_sdk/inference/nemo_inference.py` — word→segment assignment; empty-words diagnostics
- `dalston/engine_sdk/base_transcribe.py` — warn on granularity downgrade
- plus whatever 92.1 identified as the actual vocabulary/timestamps interaction fix in `batch_engine.py`

**Deliverables:**

1. Apply 92.1's root-cause fix (if vocab interaction: re-assert timestamp computation when boosting rebuilds the strategy, or configure boosting through NeMo's transcribe-config path; if representation mismatch: item 2 suffices).
2. Replace the strict bracket filter (`nemo_inference.py:661-665`) with midpoint assignment: a word belongs to the segment whose span contains `(w.start + w.end) / 2`, ties to the earlier segment; words matching no segment attach to the nearest one. This survives sloppy NeMo segment spans instead of silently dropping words.
3. When `transcribe(timestamps=True)` yields segments but zero words, log WARNING (`nemo_word_timestamps_missing`, model id + decoding strategy) and append a transcript warning — the signal that was absent on the failing job.
4. In `build_transcript` (`base_transcribe.py:531-534`), when the engine advertises `supports_word_timestamps=True` but granularity degrades to SEGMENT, append a transcript warning instead of downgrading silently. Reaches the response via 92.2.

---

### 92.7: Speech-coverage accounting + honest segment bounds (F6, F7)

**Files modified:**

- `dalston/engine_sdk/inference/nemo_inference.py` — segment-end clamping to last word
- `dalston/engine_sdk/inference/onnx_inference.py` — segment-end clamping to last token
- `dalston/engine_sdk/vad.py` + `dalston/engine_sdk/base_transcribe.py` — no-speech warnings; VAD threshold knob
- `engines/stt-prepare/audio-prepare/engine.py` + `dalston/orchestrator/dag.py` — speech-region detection for transcription jobs
- `dalston/common/transcript.py` — coverage check at assembly

**Deliverables:**

1. **Clamp segment ends to recognized content.** NeMo: when a segment has words, `end = max(end_of_last_word, start)` (effective only once 92.6 restores words — clamping cannot repair a wordless transcript, hence the step ordering). ONNX: when token timestamps exist, use last-token end (absolute) instead of the VAD region boundary. VAD/hypothesis-boundary end survives only when no finer signal exists.
2. **Coverage check grounded in prepare-stage VAD, not raw durations.** A naive `decoded_duration / file_duration` ratio warns on every long silence or hold-music stretch, and neither VAD path currently exposes a waveform for ad-hoc RMS checks (the chunker returns only chunk files; ONNX hands a filename to onnx-asr). Instead use data the pipeline already models: `PreparationResponse.speech_regions` / `speech_ratio` (existing fields). Enable `detect_speech_regions` in the prepare stage for transcription jobs (one Silero pass over audio prepare already decodes; per-channel: per channel file). At assembly, compute **missed speech** = total duration of prepare speech regions not overlapped by any transcribed segment span (±1 s tolerance). When missed speech exceeds a threshold (default: >3 s AND >10% of detected speech), append: `"N.N s of detected speech was not transcribed (regions: 34.0–37.5 s, 48.0–56.0 s)"`. This criterion fires on the failing call (loud undecoded turns) and stays quiet on ordinary pauses. Silero can still miss speech that prepare's VAD also misses — that residual risk is accepted and mitigated by (4).
3. **Empty-transcript warning.** `VadChunker` no-speech (`vad.py:375-377`) → `base_transcribe.py:320-326` empty merge, and ONNX with zero VAD segments, produce a transcript with warning `"no speech detected in X s of audio"` rather than a silently empty result.
4. **Tunable VAD for narrowband audio.** Expose `DALSTON_VAD_THRESHOLD` (default 0.5) wired into both `VadChunker` (`common/audio_defaults.py`) and the ONNX `with_vad` call (currently hardcoded, `onnx_inference.py:272-277`) and prepare's speech-region detection, so all three Silero consumers share one knob. Document 0.3 as the telephony starting point.
5. **Fix ONNX duration telemetry** (`onnx_inference.py:310`): report actual file duration (probe/soundfile), not max-VAD-segment-end, so RTF and the coverage picture stop hiding trailing drops.

Validate against the real failing WAV: the 34–37.5 s and 48–56 s turns must either transcribe (threshold tuning) or produce the missed-speech warning.

---

### 92.8: NeMo confidence emission (F5) — enhancement

**Files modified:**

- `dalston/engine_sdk/inference/nemo_inference.py`, `engines/stt-transcribe/nemo/batch_engine.py`

**Deliverables:**

Enable NeMo's confidence estimation (`ConfidenceConfig` with `preserve_word_confidence=True` on the decoding config — supported by NeMo's ASR API, values land on the hypothesis), populate `NeMoWordResult.confidence` and a per-segment mean into segment metadata as `confidence`. Do **not** fabricate `avg_logprob`/`no_speech_prob` — those stay Whisper-only and null. Gate on measured overhead (<5% RTF); ship last and independently since it changes decoding config (interacts with the F4 fix — must land after 92.6 and rerun its tests).

---

## Non-Goals

- **Engine-selector language routing** (prefer `supports_language_forcing` engines for `language`-pinned jobs) — real feature, separate milestone; needs product decision on failure semantics. Note the selector already *rejects* catalog-incompatible model+language combinations; this non-goal is only about preference among compatible engines.
- **Recording-path audio quality** — the 2 kHz low-pass is the caller's carrier/PBX; nothing server-side recovers it.
- **LID model for language_confidence** — honesty first (null); computing real LID confidence is future work.
- **Resurrecting `final-merger._merge_per_channel`** — it stays dead; deleting it is a cleanup PR, not part of this milestone.
- **Realtime (rt_engine.py) parity** — same F1/F2 issues exist there; fold into the streaming milestone to keep this one batch-scoped.

---

## Deployment

92.4 and 92.5 change `pipeline_types.py` models (one `PIPELINE_SCHEMA_VERSION` bump covers both if landed together). Standard typed-contract order applies: deploy engines first, orchestrator second. Old consumers of `PreparationResponse` degrade silently (`_get_typed_output` returns `None` on validation failure) — acceptable for the transition window, invisible afterward.

---

## Verification

```bash
make dev-gpu   # nemo engine requires GPU

export DALSTON_API_KEY=...   # from .env

# F3/F2/F1/F9: run per-channel job on the stereo 8k fixture (waits for completion by default)
JOB=$(dalston transcribe tests/fixtures/audio/stereo_8k_narrowband.wav \
  --model nvidia/parakeet-tdt-0.6b-v3 --speakers per-channel --language hr \
  -v Bizzon -v AgentCASH --json | jq -r .id)
RESP=$(curl -s http://localhost:8000/v1/audio/transcriptions/$JOB \
  -H "Authorization: Bearer $DALSTON_API_KEY")

echo "$RESP" | jq '.metadata | {audio_channels, sample_rate}'
# PASS: {"audio_channels": 2, "sample_rate": 8000}

echo "$RESP" | jq '.metadata | {language_confidence, language_source}'
# PASS: {"language_confidence": null, "language_source": "requested"}

echo "$RESP" | jq '.metadata.pipeline_warnings'
# PASS: contains "cannot force language"; contains vocabulary warning if
#       boosting unsupported in the image

# F4: word timestamps survive vocabulary boosting
echo "$RESP" | jq '[.segments[].words // [] | length] | add'
# PASS: > 0

# F7: segment ends clamped to recognized content
echo "$RESP" | jq '.segments[] | select(.words != null and (.words | length) > 0) | (.end - .words[-1].end)'
# PASS: every value <= 0.5

# F6: coverage warning — rerun the same command against the archived support-call WAV
# PASS: pipeline_warnings mentions untranscribed detected speech
#       OR the 34-56s turns transcribe at the tuned DALSTON_VAD_THRESHOLD
```

---

## Checkpoint

- [ ] 92.1: fixture committed; F4 root cause written up (raw timestamp values logged); F6 mechanism on the real WAV identified (mistranscription vs decode truncation); GPU-PB support in shipped image confirmed/denied
- [ ] 92.2: engine warnings reach `pipeline_warnings` in both assembly paths (incl. successful-align warnings); boosting failure is loud, leaves decoding config unmutated, and leaks no temp file
- [ ] 92.3: `supports_language_forcing` capability; `language_source` provenance in metadata; cannot-force warning; docstrings corrected
- [ ] 92.4: `language_confidence` null end-to-end for NeMo **and** ONNX; ElevenLabs `0.0` concession documented; `PIPELINE_SCHEMA_VERSION` bumped; SDK/CLI audited
- [ ] 92.5: stereo 8 kHz job reports `audio_channels: 2`, `sample_rate: 8000`; `source_media` sourced from the prepare request payload, not the probe
- [ ] 92.6: words populated with vocabulary enabled; granularity downgrade warns
- [ ] 92.7: segment ends clamped; speech-region-based missed-speech warning fires on the real WAV or its turns transcribe; no-speech warnings; `DALSTON_VAD_THRESHOLD` wired into all three Silero consumers; ONNX duration telemetry reports file duration
- [ ] 92.8: word/segment confidence emitted by NeMo within RTF budget
