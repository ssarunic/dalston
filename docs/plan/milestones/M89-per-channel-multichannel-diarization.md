# M89: Per-Channel Multichannel Transcription with Diarization

|                    |                                                              |
| ------------------ | ------------------------------------------------------------ |
| **Goal**           | Transcribe multichannel audio (stereo / N-channel contact-center recordings) with each channel routed as a distinct speaker, **with** diarization enabled and **without** ElevenLabs' 5-channel cap |
| **Duration**       | 5–8 days                                                     |
| **Dependencies**   | M04 (Speaker Diarization — complete), M62 G-17 (multi-channel parity) which tracks the surface-level field but not the fan-out path |
| **Deliverable**    | `use_multi_channel` request field, per-channel task fan-out in orchestrator, merged response schema, capability declaration on engines |
| **Status**         | Not Started                                                  |

## User Story

> *"As a contact-center engineer, I upload a 2-channel WAV where channel 0 is the agent and channel 1 is the customer, and I want a single transcript back with every word already attributed to the right speaker — without running a clustering model, and without ElevenLabs' limitation that I can't have both multichannel and diarization."*

---

## Outcomes

| Scenario | Current | After M89 |
| -------- | ------- | --------- |
| Stereo call recording (agent L / customer R) | Dalston downmixes to mono, diarizes with pyannote, hopes the clustering guesses right | Each channel transcribed independently, channel index is the speaker — no clustering, no guessing |
| 8-channel conference room recording | `use_multi_channel` field accepted but silently ignored (see PARITY_GAPS.md G-17) | Fans out to 8 parallel transcribe tasks, merged into one transcript with 8 speakers |
| Request `use_multi_channel=true` + `diarize=true` on ElevenLabs route | Scribe: not supported (docs explicitly disable diarization with multichannel). Dalston today: the combination silently does the wrong thing | Both flags supported simultaneously; per-channel diarization is trivially correct because each channel is one speaker by construction |
| 2-channel file with 30 min of cross-talk | Diarization rate limited by clustering accuracy (WER ~10% on cross-talk) | Cross-talk handled naturally — each speaker has their own audio stream, no attribution ambiguity |

---

## Motivation

The 2026 ElevenLabs complaint set includes a hard feature cap that multiple comparison posts called out: **Scribe v2 limits multichannel files to 5 channels AND disables diarization on multichannel**. For contact centers — the single largest STT vertical outside consumer voice agents — that's a blocker.

Dalston has two structural advantages:

1. **Each channel in a stereo/N-channel recording is, by definition, one speaker.** There is no clustering problem: channel 0 is speaker 0, channel 1 is speaker 1. The "diarization" result comes for free as a side effect of the fan-out.
2. **Dalston's orchestrator already fans out parallel tasks per pipeline stage.** Adding a per-channel fan-out is an orchestrator change, not a new subsystem.

M62 G-17 flagged `use_multi_channel` as `NEW-CAPABILITY` and deferred it. M89 actually builds it — and because it's a per-channel fan-out rather than new model work, it's cheaper than G-17 implied. The output is a superset of what Scribe v2 offers: any channel count, diarization always-on, correct cross-talk handling.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    MULTICHANNEL FAN-OUT                                  │
│                                                                          │
│   upload 8-channel WAV                                                   │
│        │                                                                 │
│        ▼                                                                 │
│   ┌─────────────────┐                                                   │
│   │  PREPARE stage  │  demux into N mono WAVs                            │
│   │  (audio-prepare)│  artifacts: ch0.wav, ch1.wav, … chN.wav            │
│   └────────┬────────┘                                                   │
│            │                                                             │
│            ▼                                                             │
│   ┌─────────────────────────────────────────────────────┐               │
│   │         TRANSCRIBE stage — fan-out                   │               │
│   │                                                      │               │
│   │   ch0 ──▶ transcribe task ──▶ transcript_0          │               │
│   │   ch1 ──▶ transcribe task ──▶ transcript_1          │               │
│   │   …                                                  │               │
│   │   chN ──▶ transcribe task ──▶ transcript_N          │               │
│   └────────────────────┬─────────────────────────────────┘               │
│                        │                                                 │
│                        ▼                                                 │
│   ┌─────────────────────────────────────────┐                           │
│   │  MERGE stage — multichannel merger      │                           │
│   │                                          │                           │
│   │  interleave segments by start time       │                           │
│   │  each segment tagged speaker = channel_i  │                           │
│   │  emit unified Transcript                  │                           │
│   └─────────────────────────────────────────┘                           │
│                                                                          │
│   Align, PII-detect, redact run AFTER the merge on the unified           │
│   transcript (no need to fan those out).                                 │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## Steps

### 89.1: `audio-prepare` gains a channel-demux mode

**Files modified:**

- `engines/stt-prepare/audio-prepare/engine.py` — new `demux_channels` action
- `engines/stt-prepare/audio-prepare/engine.yaml` — capability declaration

**Deliverables:**

When the incoming task carries `use_multi_channel=true`, the prepare engine demuxes the input file into N mono WAVs and emits them as task artifacts. For mono or disabled multichannel, the existing single-file behavior is preserved.

```python
# engines/stt-prepare/audio-prepare/engine.py

def _demux_channels(self, src: Path, temp_dir: Path) -> list[Path]:
    """Split a multichannel file into N mono WAVs. Returns paths in channel order."""
    info = sf.info(src)
    if info.channels == 1:
        return [src]  # passthrough
    audio, sr = sf.read(src, always_2d=True)  # (frames, channels)
    outputs = []
    for ch_idx in range(info.channels):
        out = temp_dir / f"{src.stem}_ch{ch_idx}.wav"
        sf.write(out, audio[:, ch_idx], sr, subtype="PCM_16")
        outputs.append(out)
    return outputs
```

**Decisions:**

- Use `soundfile` + `numpy` — already a dependency of `audio-prepare`.
- Output format is PCM16 @ original sample rate. Downstream transcribe engines handle resampling.
- Hard cap: **64 channels**. Beyond that is a misconfiguration. Reject with a clean 400 at the gateway.

---

### 89.2: Gateway accepts `use_multi_channel` and forwards to DAG builder

**Files modified:**

- `dalston/gateway/models/requests.py` — add `use_multi_channel: bool = False`
- `dalston/gateway/api/v1/transcription.py` — propagate to orchestrator job
- `dalston/gateway/api/v1/speech_to_text.py` — ElevenLabs-compat route
- `tests/unit/test_request_validation.py` — validation tests

**Deliverables:**

`use_multi_channel` is a boolean flag in the batch request. When `true`, the gateway:

1. Probes the uploaded file with `soundfile.info` (already done for duration detection).
2. If `channels == 1`, rejects the request with a clear 400: "use_multi_channel requested but file is mono".
3. If `channels > 64`, rejects with 400.
4. Otherwise, stamps `job.use_multi_channel = true` and `job.channel_count = N` onto the orchestrator job.

**Validation contract:**

```python
class TranscribeRequest(BaseModel):
    use_multi_channel: bool = Field(
        default=False,
        description=(
            "Treat each channel of a multichannel upload as a distinct speaker. "
            "Fans out transcription per channel. Requires channels >= 2, <= 64."
        ),
    )
```

---

### 89.3: DAG builder emits per-channel transcribe tasks

**Files modified:**

- `dalston/orchestrator/dag.py` — `_build_transcribe_stage` branches on `use_multi_channel`
- `tests/unit/test_dag_multichannel.py` *(new)*

**Deliverables:**

When `job.use_multi_channel` is true, the DAG builder creates **N parallel transcribe tasks**, each pointing at a different channel artifact from the prepare stage, each tagged with `channel_index`. They share a fan-in node that is the new merge stage (89.5).

```python
# Simplified sketch
def _build_transcribe_stage(self, job, prepare_artifacts):
    if not job.use_multi_channel:
        return [self._single_transcribe_task(job, prepare_artifacts.audio_path)]

    tasks = []
    for ch_idx, ch_path in enumerate(prepare_artifacts.channel_paths):
        tasks.append(
            TranscribeTask(
                job_id=job.id,
                audio_path=ch_path,
                channel_index=ch_idx,
                # speaker_id is deterministic from channel_index
                speaker_hint=f"channel_{ch_idx}",
            )
        )
    return tasks
```

**Align / PII / redact stages are NOT fanned out.** They run once after the merge, on the unified transcript. That keeps the common case fast and avoids N× copies of the align model.

**Diarize stage is skipped entirely.** When `use_multi_channel=true`, diarization is trivially solved: channel index **is** the speaker. The DAG builder shortcut: skip the diarize node, the merge node stamps speakers directly.

---

### 89.4: Transcribe engine stamps `channel_index` on all segments

**Files modified:**

- `dalston/engine_sdk/base_transcribe.py` — read `task_request.channel_index`, stamp it on the Transcript
- `dalston/common/types.py` — `Segment.channel_index: int | None`

**Deliverables:**

Every segment that comes out of a per-channel transcribe task carries `channel_index`. The merge stage uses this as the speaker id. For non-multichannel jobs `channel_index` is `None` and the pipeline behaves exactly as today.

```python
class Segment(BaseModel):
    start: float
    end: float
    text: str
    words: list[Word] | None = None
    speaker: str | None = None
    channel_index: int | None = None  # NEW
```

---

### 89.5: Multichannel merger stage

**Files modified:**

- `engines/stt-merge/final-merger/engine.py` — extend to handle N channel transcripts
- `tests/unit/test_multichannel_merge.py` *(new)*

**Deliverables:**

When the merge stage sees N transcribe results with `channel_index` set, it interleaves them by `start` time and stamps `speaker = f"speaker_{channel_index}"` on every segment. Cross-talk (simultaneous speech on multiple channels) is preserved: both speakers get their own segments with overlapping time ranges.

```python
def merge_multichannel(channel_transcripts: list[Transcript]) -> Transcript:
    all_segments = []
    for ch_idx, tr in enumerate(channel_transcripts):
        for seg in tr.segments:
            seg.speaker = f"speaker_{ch_idx}"
            seg.channel_index = ch_idx
            all_segments.append(seg)
    all_segments.sort(key=lambda s: (s.start, s.channel_index))
    return Transcript(
        segments=all_segments,
        text=_interleave_text(all_segments),
        language=channel_transcripts[0].language,
        # each channel has its own language_confidence; take min as conservative
        language_confidence=min(t.language_confidence or 1.0 for t in channel_transcripts),
    )
```

**Text rendering:** The flat `text` field prefixes each speaker turn with `[speaker_0]` / `[speaker_1]` markers. This matches Dalston's existing diarized export format.

---

### 89.6: Response schema — `speakers` always populated on multichannel

**Files modified:**

- `dalston/schemas/transcript.py` — response serialization
- `dalston/gateway/api/v1/export.py` — diarized export format for multichannel

**Deliverables:**

The transcript response for a multichannel job always includes a populated `speakers` array with N entries, one per channel, each carrying `speaker_id`, `channel_index`, and total speaking duration. This gives contact-center dashboards the numbers they care about (talk ratio, silence gaps) without a post-processing step.

```json
{
  "transcript_id": "tr_abc",
  "channels": 2,
  "speakers": [
    {"speaker_id": "speaker_0", "channel_index": 0, "talk_time_s": 84.3},
    {"speaker_id": "speaker_1", "channel_index": 1, "talk_time_s": 112.7}
  ],
  "segments": [...]
}
```

Export formats (SRT, VTT, JSON, diarized_json) all honor the new speaker labels.

---

## Non-Goals

- **Auto-detecting which channel is which speaker role** (agent vs customer) — That's a business-logic concern. Dalston returns `speaker_0` / `speaker_1`; the caller maps them to roles.
- **Cross-channel echo cancellation** — If channel 0 bleeds into channel 1, both transcripts will carry the bleed. Real contact-center recordings are usually clean; if not, users pre-process before upload.
- **Per-channel engine choice** — All channels use the same transcribe engine. Letting channel 0 use `whisper-large` and channel 1 use `parakeet` is theoretically interesting but adds scheduling complexity with no clear demand.
- **Multichannel realtime** — Batch only. Realtime multichannel is a different beast (worker per channel, sync'd by timestamp). Track separately.
- **M62 G-17 the surface-field wiring** — G-17 is the gateway-level acceptance of the field. M89 depends on it landing or subsumes it, whichever sequencing wins.

---

## Deployment

Rolling deploy. The new field defaults to `false`, so existing clients see no change. The orchestrator DAG builder's multichannel branch only activates when the flag is set. Prepare engines built before M89 won't have `demux_channels` — they fall through to the mono path, which is still correct when `use_multi_channel=false`.

Worth noting: the prepare stage's demux produces N files on disk temporarily. For a 1-hour 8-channel 16 kHz PCM16 file that's ~920 MB of intermediate artifacts. The orchestrator's scratch directory sizing assumptions in `docs/specs/batch/STORAGE.md` should be reviewed if heavy multichannel use is expected.

---

## Verification

```bash
make dev

# 1. Stereo call recording → 2 speakers, diarization correct
curl -X POST http://localhost:8000/v1/audio/transcriptions \
  -H "Authorization: Bearer $DALSTON_API_KEY" \
  -F "file=@tests/fixtures/audio/stereo-call.wav" \
  -F "use_multi_channel=true" \
  -F "diarize=true" \
  | jq '{channels, speakers}'

# Expected:
# { "channels": 2, "speakers": [{"speaker_id": "speaker_0", ...}, ...] }

# 2. 8-channel file fans out correctly
curl -X POST http://localhost:8000/v1/audio/transcriptions \
  -H "Authorization: Bearer $DALSTON_API_KEY" \
  -F "file=@tests/fixtures/audio/8ch-conference.wav" \
  -F "use_multi_channel=true" \
  | jq '.speakers | length'
# Expected: 8

# 3. Mono file with flag set → clean 400
curl -X POST http://localhost:8000/v1/audio/transcriptions \
  -H "Authorization: Bearer $DALSTON_API_KEY" \
  -F "file=@tests/fixtures/audio/mono.wav" \
  -F "use_multi_channel=true"
# Expected: 400, "use_multi_channel requested but file is mono"

# 4. Channel cap enforced
# Synthetic 65-channel WAV → 400

# 5. Regression: mono file without flag works exactly as before
curl -X POST http://localhost:8000/v1/audio/transcriptions \
  -H "Authorization: Bearer $DALSTON_API_KEY" \
  -F "file=@tests/fixtures/audio/mono.wav"
# Expected: normal transcription, speakers populated via diarize stage
```

---

## Checkpoint

- [ ] **89.1** `audio-prepare` demuxes multichannel input into N mono WAVs
- [ ] **89.2** `use_multi_channel` accepted on Dalston native + ElevenLabs routes
- [ ] **89.2** Mono file + flag returns 400
- [ ] **89.2** >64 channels returns 400
- [ ] **89.3** DAG builder fans out per-channel transcribe tasks when flag set
- [ ] **89.3** Diarize stage skipped on multichannel jobs
- [ ] **89.4** `channel_index` stamped on segments
- [ ] **89.5** Merger interleaves channels by `start` time and assigns deterministic speaker IDs
- [ ] **89.6** Response schema populates `speakers` with per-channel talk time
- [ ] Export formats (SRT/VTT/diarized_json) honor multichannel speakers
- [ ] 8-channel integration test end-to-end with real conference-room audio
- [ ] Regression: single-channel jobs unchanged
