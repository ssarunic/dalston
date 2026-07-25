# Picking the right transcription + diarization combo

<!-- performance-data: estimate; source=engine.yaml planning metadata -->

> Three decisions, two patterns. Walking through them once will save you
> hours of "why is my speaker labeling weird" later.

If you only need transcription (no speakers), you have one choice: pick a
transcribe preset from [12-engine-presets-catalog.md](12-engine-presets-catalog.md).
This page is for when you need **transcribe + speakers + (optionally) word
timestamps**.

---

## The three decisions

1. **Language?** — drives the transcribe engine choice.
2. **One GPU or many?** — drives split vs combo deployment.
3. **Real-time or batch?** — drives streaming vs forced-alignment word timestamps.

---

## Decision 1: language

| Languages | Best transcribe engine | Why |
|---|---|---|
| English only | `nemo` | RTF 0.0006, native streaming, native word timestamps |
| 99 languages, including non-Latin | `faster-whisper` (large-v3-turbo) | Whisper's strength is language coverage |
| Custom / fine-tuned model | `hf-asr` | Any HF model with `pipeline_tag=automatic-speech-recognition` |
| Multilingual + reasoning over audio | `vllm-asr` (Voxtral) | Audio LLM, longer context, multilingual |
| English + lightweight | `onnx` | Smallest container, CPU-OK |

For **diarization**, `pyannote-4.0` is the default. `nemo-msdd` and
`nemo-sortformer` are also registered options when their NeMo models and
hardware profile suit the deployment. Diarization is language-independent: it
operates on speech characteristics, not transcript words.

---

## Decision 2: one GPU or many?

### Single GPU → use the **combo engine**

The `hf-asr-align-pyannote` engine ([engine.yaml](../../engines/stt-transcribe/hf-asr-align-pyannote/engine.yaml))
runs HuggingFace ASR + phoneme align + pyannote in a single Python process.

```
hf-asr-align-pyannote
├── min_vram_gb: 10
├── min_ram_gb: 16
├── max_concurrency: 1
├── word_timestamps: true
├── includes_diarization: true   ← orchestrator skips DIARIZE stage
└── recommended_gpu: a10g, l4
```

Use cases:

- Mac development with MPS
- Single-GPU production where you want one container managing one VRAM budget
- Demos where you don't want to coordinate three services

Check the dated cost estimator for the current instance-price assumptions.

### Multiple GPUs → run **separate engines**

This is the production shape: one engine per stage, scaled independently.

```
faster-whisper (g4dn.xlarge spot)  ─┐
phoneme-align (g4dn.xlarge spot)   ─┼─► assembled by orchestrator
pyannote-4.0 (g4dn.xlarge spot)    ─┘
```

Three boxes doing different stages in parallel.
Higher throughput, more flexibility, easier to scale a hot stage.

Or: **one box, multiple engines co-located** on a g6.xlarge (24 GB L4),
which the `dalston-aws` GPU presets are tuned for:

```
nemo + pyannote → one g6.xlarge spot
```

The presets calculate per-engine budgets from the selected GPU family. For
example, an L4 worker can allocate a larger NeMo and pyannote budget than a T4
worker. Treat the calculated values as caps for that deployment, not universal
20 GB/4 GB requirements.

```bash
dalston-aws launch gpu --engines nemo,pyannote --spot
```

---

## Decision 3: real-time or batch?

### Batch — forced alignment for word timestamps

If you submit a complete file and want word-level timestamps, you have two
paths:

- Use a transcribe engine that produces word timestamps **natively**
  (`nemo`, `onnx`, Whisper-via-`hf-asr`). No ALIGN stage needed.
- Use `faster-whisper` (which doesn't) and add a **`phoneme-align` stage**.
  More accurate alignment than Whisper's built-in word timestamps.

### Real-time — only natively-streaming engines

Real-time WebSocket sessions only work with engines that declare
`native_streaming: true` and have a real-time mode:

- `nemo` — best for English, ~100 ms end-to-end latency
- `onnx` — second-best, with word timestamps
- `faster-whisper` — VAD-chunked streaming
- `hf-asr` — generic
- `vllm-asr` — audio LLM streaming
- `riva` — proprietary

`pyannote` does not stream (`native_streaming: false`). For real-time
diarization you have two options:

1. **Record and submit separately:** transcribe live for immediate captions,
   retain the recording, then submit it as a batch job with
   `speaker_detection=diarize`. Realtime completion does not automatically
   create a diarization job.
2. **Per-channel split:** if your audio source already has one speaker per
   channel (call centers, multi-mic recording), use
   `speaker_detection=per-channel` — no diarization model needed, the
   channel *is* the speaker.

---

## Common combos and throughput

The `engine.yaml` performance fields are planning estimates, not reproducible
benchmarks. Use them to shortlist a topology, then measure with your audio:

| Combo | Hardware pattern | Scheduling behavior |
| --- | --- | --- |
| `nemo` only | One compatible GPU | Transcription only |
| `nemo` + `pyannote` co-located | One GPU with shared VRAM budget | Transcription and diarization can overlap |
| `faster-whisper` + `phoneme-align` + `pyannote` | Separate compatible workers | Parallel branches, then orchestrator assembly |
| `hf-asr-align-pyannote` | One larger GPU | Sequential composite process |
| `whisper-align-pyannote` composite | Three child engines | Parallel transcription/diarization, then alignment |

For an actual benchmark, record hardware, model revisions, engine commit,
audio corpus, concurrency, and date. For prices, use
[51-aws-cost-estimator.md](51-aws-cost-estimator.md).

---

## Quality knobs that matter

### `min_speakers` / `max_speakers` / `num_speakers`

If you know the expected speaker count — interview with 2 people, panel
with 4 — pass it in. Pyannote 4.0's clustering uses these as hints and is
notably more accurate when constrained.

```python
client.transcribe(
    "interview.mp3",
    speaker_detection="diarize",
    num_speakers=2,           # exact
    # or:
    # min_speakers=2, max_speakers=4
)
```

### `exclusive=true` (pyannote 4.0 feature)

When you want each segment to have **exactly one speaker** — better for
Whisper alignment, simpler downstream. The combo engine sets this; for
separate engines, configure via the diarize stage parameters.

### Vocabulary boosting

Domain terms can be boosted on transcribe:

```python
client.transcribe(
    "meeting.mp3",
    vocabulary=["PostgreSQL", "Kubernetes", "Tailscale"],
)
```

Max 100 terms, 50 chars each. The engines that support it: `faster-whisper`,
`hf-asr` (Whisper variants).

---

## Common mistakes

- **Using `faster-whisper` and complaining about word timestamps.** It doesn't
  produce them. Add an `align` stage or pick a different engine.
- **Running pyannote on CPU and expecting GPU-class throughput.** Benchmark the
  chosen model and corpus before sizing a production worker.
  Use a GPU.
- **`HF_TOKEN` not set** — pyannote will refuse to load. See
  [30-how-models-are-fetched.md](30-how-models-are-fetched.md).
- **Running NeMo on non-English audio** — it's English-only. Use Whisper or
  Voxtral.
- **Using vllm-asr on a T4** — compute 7.5 < 8.0 required. Use g6.xlarge
  (L4) or g5.xlarge (A10G).

---

## See also

- [12-engine-presets-catalog.md](12-engine-presets-catalog.md) — every preset, every number
- [31-pipeline-stages-explained.md](31-pipeline-stages-explained.md) — what each stage does
- [50-performance-and-rtf.md](50-performance-and-rtf.md) — RTF math
- [`docs/specs/MODEL_SELECTION.md`](../specs/MODEL_SELECTION.md) — engineering reference
