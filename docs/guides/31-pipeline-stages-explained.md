# Pipeline stages explained

Dalston builds a task graph for each request. The normal distributed batch
pipeline has four inference stages, two of them optional:

```text
prepare ──► transcribe ──► optional align
   │
   └─────────────────────► optional diarize
```

Diarization depends only on prepared audio, so it runs in parallel with
transcription and alignment. When all required tasks finish, the orchestrator
assembles the canonical transcript. There is no distributed merge task.

PII work is asynchronous post-processing after the transcript is assembled:

```text
assembled transcript ──► optional pii_detect ──► optional audio_redact
```

## Prepare — `audio-prepare`

Source: [`engines/stt-prepare/audio-prepare/`](../../engines/stt-prepare/audio-prepare/).

Prepare probes the original media, records its duration/sample rate/channels
and codec, then normalizes audio for downstream engines. It can also split
channels for `speaker_detection=per_channel`.

Input may be any format supported by ffmpeg. The standard speech artifact is
16 kHz, mono, 16-bit PCM WAV. This CPU stage is always present.

## Transcribe — choose an ASR engine

Transcription converts prepared audio into the canonical `Transcript` type:
text, timed segments, language information, confidence where the engine
provides it, warnings, and sometimes word timestamps.

Common engine families:

| Engine | Typical use |
| --- | --- |
| `onnx` | Lightweight Parakeet models, including CPU execution |
| `faster-whisper` | Broad multilingual Whisper support |
| `nemo` | High-throughput NeMo/Parakeet models |
| `hf-asr` | Custom Hugging Face ASR models |
| `vllm-asr` | Audio language models served through vLLM |

See [12-engine-presets-catalog.md](12-engine-presets-catalog.md) for deployment
presets. Performance values in the engine catalog are benchmark metadata, not
a guarantee for every model, file, or machine.

Batch transcription engines can opt into SDK-managed VAD chunking by declaring
a maximum audio duration. Audio above that limit is split on speech boundaries,
each chunk is transcribed, and timestamp offsets are recombined. Engines with no
declared limit keep the direct whole-file path; chunk limits may also vary by
the selected model.

## Align — `phoneme-align`

Source: [`engines/stt-align/phoneme-align/`](../../engines/stt-align/phoneme-align/).

Alignment refines segment timing to word timing using the prepared audio and
the transcription response. It is added only when word timestamps were
requested and the selected transcription engine does not provide them
natively.

NeMo and ONNX model variants commonly provide native word timing.
`faster-whisper` is currently advertised without reliable native word timing,
so capability-based selection normally adds `phoneme-align` for word-level
requests.

## Diarize — choose a speaker engine

Source: [`engines/stt-diarize/`](../../engines/stt-diarize/).

Diarization produces speaker turns such as
`{start, end, speaker}` from prepared audio. Available implementations include:

| Engine | Notes |
| --- | --- |
| `pyannote-4.0` | Default pyannote community pipeline |
| `nemo-msdd` | NeMo MSDD diarization |
| `nemo-sortformer` | NeMo Sortformer diarization |

The task is present only for `speaker_detection=diarize` and only when the
selected transcription engine does not already include speaker labels.
`num_speakers`, `min_speakers`, and `max_speakers` constrain compatible
engines.

Pyannote requires `HF_TOKEN` for gated models. Long input is chunked according
to `DALSTON_MAX_DIARIZE_CHUNK_S`; the engine can reduce its chunk size after a
CUDA out-of-memory error.

## Transcript assembly

Assembly is orchestrator business logic, not a queue-backed engine stage. It
combines the latest transcription/alignment result with diarization turns,
assigns speakers by time overlap, preserves confidence and warnings, and writes
the final transcript artifact.

For per-channel audio, the orchestrator assembles the independently
transcribed channels into one chronological transcript. No `final-merger`
worker is required.

The repository retains legacy merge contracts and a lite-profile merge
implementation for compatibility. Do not deploy or diagram `final-merger` as
part of the distributed DAG.

## PII detection and audio redaction

When requested, post-processing runs after successful transcript assembly:

1. `pii_detect` identifies configured entity types and produces redacted text.
2. `audio_redact` uses timed entities to generate silenced or beeped audio.

These stages do not delay the core transcription result. Their state and
artifacts appear as post-processing becomes available.

## Capability-driven shortcuts

An engine can advertise native word timestamps, included diarization, native
streaming, and language-forcing support. The orchestrator uses those
capabilities to omit redundant tasks; it does not infer support from an engine
name.

Examples:

| Request and selected capability | Distributed DAG |
| --- | --- |
| Segment timestamps, no speakers | `prepare → transcribe` |
| Word timestamps, no native word support | `prepare → transcribe → align` |
| Diarization | `prepare → transcribe`, plus `prepare → diarize` |
| Word timestamps and diarization | `transcribe → align` in parallel with `diarize` |
| Native word timestamps | `prepare → transcribe` |
| Included diarization | Omit the separate diarize task |
| Per-channel | `prepare → transcribe_chN → optional align_chN` for each channel |

After all terminal tasks complete, the orchestrator assembles the result and
emits job completion.

## See also

- [12-engine-presets-catalog.md](12-engine-presets-catalog.md)
- [32-diarization-vs-transcription.md](32-diarization-vs-transcription.md)
- [30-how-models-are-fetched.md](30-how-models-are-fetched.md)
- [PIPELINE_INTERFACES.md](../specs/PIPELINE_INTERFACES.md)
- [ORCHESTRATOR.md](../specs/batch/ORCHESTRATOR.md)
