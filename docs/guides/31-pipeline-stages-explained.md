# Pipeline stages explained

> Five stages, each one focused on a single job. Together they turn raw
> audio into a speaker-attributed, word-timed transcript. Mix and match
> engines per stage for the best price-performance fit.

```
┌─────────┐   ┌─────────────┐   ┌────────┐   ┌─────────┐   ┌────────┐
│ PREPARE │──►│ TRANSCRIBE  │──►│ ALIGN  │──►│ DIARIZE │──►│ MERGE  │
└─────────┘   └─────────────┘   └────────┘   └─────────┘   └────────┘
```

The orchestrator builds a per-job DAG over these stages based on what you
asked for. If you didn't ask for word timestamps, ALIGN is skipped. If you
didn't ask for diarization, DIARIZE is skipped. MERGE is always last and
always assembles the canonical `transcript.json`.

---

## PREPARE — `audio-prepare`

> Make any audio look the same to every downstream stage.

Source: [`engines/stt-prepare/audio-prepare/`](../../engines/stt-prepare/audio-prepare/).

**In:** any audio format ffmpeg can read — mp3, wav, m4a, flac, ogg, webm,
mp4, mkv, avi, aac, wma. Any sample rate, any channel count, up to 4 hours.

**Out:** 16 kHz mono WAV, 16-bit PCM. Plus metadata: original duration,
sample rate, channels, codec.

**How:** wraps ffprobe (for metadata) and ffmpeg (for resampling/downmixing)
with a 30-minute timeout per file. CPU only — no GPU needed.

**Cost:** sub-cent per hour of audio. This stage runs on the control plane's
CPU engines; you don't need to think about it.

**Why this stage exists:** every transcription model expects a specific
input shape. Standardizing once means every engine downstream works the
same way regardless of source.

---

## TRANSCRIBE — choose your engine

> Audio → text + segment boundaries + (sometimes) word timestamps.

The variants and their tradeoffs are catalogued in
[12-engine-presets-catalog.md](12-engine-presets-catalog.md). Quick recap:

| Preset | Best for |
|---|---|
| `onnx` | Lightweight, English, CPU-OK |
| `faster-whisper` | 99 languages, good baseline |
| `nemo` | Fastest English, real-time native |
| `hf-asr` | Custom HuggingFace models |
| `vllm-asr` | Audio LLMs (Voxtral) |

**In:** 16 kHz mono WAV.
**Out:** `Transcript` object with language, segments (start/end/text), and
optional words (depending on the engine's `word_timestamps` capability).

**Cost driver:** RTF × audio duration × hourly GPU cost. NeMo on a g4dn.xlarge
spot ≈ $0.0001 per hour of audio. faster-whisper ≈ $0.05/hr of audio.

---

## ALIGN — `phoneme-align`

> Refine segment-level timestamps into word-level timestamps.

Source: [`engines/stt-align/phoneme-align/`](../../engines/stt-align/phoneme-align/).

**In:** the transcript from TRANSCRIBE plus the original audio.
**Out:** the same transcript with `Word` objects on each segment, each with
exact `start` and `end` times.

**How:** CTC forced alignment with wav2vec2 — a standalone reimplementation
of the algorithm from the WhisperX paper. Supports the major European
languages via torchaudio pipelines plus 35+ more via HuggingFace wav2vec2
models.

**Cost:** GPU optional but recommended. RTF is fast; this stage is rarely
the bottleneck.

**Skipped when:** the upstream transcribe engine already produces word
timestamps (`nemo`, `onnx`, Whisper-via-`hf-asr`) **or** you didn't request
`timestamps_granularity=word`.

> **faster-whisper specifically does not** produce reliable word timestamps
> on its own (verified in its `engine.yaml` — `word_timestamps: false`).
> Pair it with ALIGN if you need word-level timing. NeMo doesn't need it;
> ONNX doesn't need it.

---

## DIARIZE — `pyannote-4.0` (or `nemo-msdd`)

> Audio → speaker timeline. Who spoke when.

Source: [`engines/stt-diarize/pyannote-4.0/`](../../engines/stt-diarize/pyannote-4.0/).

**In:** 16 kHz mono WAV.
**Out:** `DiarizationResponse` with `speakers` (list of detected speaker
IDs like `SPEAKER_00`) and `turns` (list of `{start, end, speaker}`).

**How:** pyannote-audio 4.0 with the `pyannote/speaker-diarization-community-1`
pipeline. New in 4.0: VBx clustering for better speaker counting,
**exclusive mode** that emits one speaker per segment (cleaner Whisper
alignment), modern numpy 2.0 / PyTorch compatibility.

Long audio gets chunked: configurable via `DALSTON_MAX_DIARIZE_CHUNK_S`
(default 900s in the upstream config; the AWS preset bumps it to 3600s).
The engine has an OOM fallback that halves the chunk size and retries.

**Cost:** GPU recommended (RTF 0.15 vs 1.2 on CPU — worth ~10× the throughput).

**Skipped when:** you didn't request `speaker_detection=diarize`. (The
`per-channel` mode does speaker assignment differently — see below.)

**Required:** `HF_TOKEN` — the model is gated. See [30-how-models-are-fetched.md](30-how-models-are-fetched.md).

---

## MERGE — `final-merger`

> Assemble the canonical `transcript.json`.

Source: [`engines/stt-merge/final-merger/`](../../engines/stt-merge/final-merger/).

**In:** outputs from every upstream stage (`prepare`, `transcribe`, `align`,
`diarize`).
**Out:** `transcript.json` with:

- `metadata` — source, duration, language, model used, processing time
- `text` — full concatenated text
- `segments` — speaker-attributed, word-timed segments
- `speakers` — list of speaker IDs with statistics

**How:** combines stage outputs by walking segment boundaries and assigning
the speaker whose turn has maximum overlap. CPU only, runs on the control
plane.

For **per-channel** mode (stereo audio with one speaker per channel), MERGE
also assembles redacted mono WAVs back into a stereo file via ffmpeg.

---

## When engines combine stages

Some engines do multiple stages in one container. The `composite` /
`includes_diarization` flag tells the orchestrator to skip the merged-in
stages.

| Engine | Does | Skips |
|---|---|---|
| `hf-asr-align-pyannote` | transcribe + align + diarize | individual ALIGN, DIARIZE stages |
| `whisper-align-pyannote` | transcribe + align + diarize (composite over HTTP) | individual stages |

These exist for two reasons:

1. **Single-GPU deployments** — one container holding three models means one
   warm-start cost, one memory budget, simpler ops.
2. **Mac MPS / dev boxes** — running three Docker services with bind-mounted
   models doesn't always work nicely on macOS.

Decision tree in [32-diarization-vs-transcription.md](32-diarization-vs-transcription.md).

---

## Orchestrator DAG construction

The orchestrator looks at your request and builds the DAG dynamically:

| Request | DAG |
|---|---|
| Default (no flags) | `prepare → transcribe` |
| `timestamps_granularity=word` | `prepare → transcribe → align` (skipped if engine has word ts) |
| `speaker_detection=diarize` | `prepare → transcribe → diarize` (parallel to align) |
| Both word + diarize | `prepare → (transcribe + diarize parallel) → align → merge` |
| `speaker_detection=per-channel` | `prepare (split channels) → transcribe (each channel)` |

The DAG always ends in `merge`. Tasks run in parallel where the graph
permits; the orchestrator decides which engines handle which tasks based on
capabilities reported in the Redis registry.

---

## See also

- [12-engine-presets-catalog.md](12-engine-presets-catalog.md) — picking transcribe engines
- [32-diarization-vs-transcription.md](32-diarization-vs-transcription.md) — picking the best combo
- [30-how-models-are-fetched.md](30-how-models-are-fetched.md) — HF tokens, S3 caching
- [`docs/specs/PIPELINE_INTERFACES.md`](../specs/PIPELINE_INTERFACES.md) — wire-format reference
- [`docs/specs/batch/ORCHESTRATOR.md`](../specs/batch/ORCHESTRATOR.md) — DAG internals
