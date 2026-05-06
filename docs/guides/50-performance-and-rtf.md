# Performance and RTF — sizing your deployment

> **RTF = processing-time / audio-duration.** Lower is faster.
> RTF 0.03 means a 1-hour file finishes in 108 seconds. RTF 0.0006 means
> a 1-hour file finishes in 2 seconds of pure model compute. RTF 1.0
> means real-time — exactly the audio's wall-clock duration.

This page is the math. It pulls verified numbers straight from each
engine's `engine.yaml` `performance:` block and turns them into "how many
hours can this box transcribe per day" and "what does that cost."

---

## RTF reference table

Pulled directly from `engine.yaml` files in the repo:

| Engine | rtf_gpu | rtf_cpu | warm_start | Notes |
|---|---|---|---|---|
| `onnx` | 0.03 | 0.12 | 50 ms | Streaming, word ts |
| `faster-whisper` | 0.03 | 0.4 | 30 ms | Streaming (chunked), no native word ts |
| `nemo` | 0.0006 | — | 100 ms | GPU only, native streaming |
| `hf-asr` | ~0.1 | ~1.0 | 500 ms | Model-dependent |
| `vllm-asr` | 0.15 | — | 5000 ms | GPU only, audio LLM |
| `pyannote-4.0` | 0.15 | 1.2 | 500 ms | Diarization |
| `phoneme-align` | fast (GPU) | slower | — | Word-level forced alignment |
| `audio-prepare` | (CPU only) | (ffmpeg) | — | Format conversion |

`warm_start` is the time to first useful response after the engine has the
model loaded. **Cold start** (model download from HF + load into VRAM) is
much longer — typically 30 s to 5 min depending on engine. See
[30-how-models-are-fetched.md](30-how-models-are-fetched.md).

---

## How to use these numbers

**Single-stream throughput** (one audio at a time, GPU not parallelized):

```
audio_hours_per_wall_hour = 1 / RTF
```

| Engine | Per GPU per hour |
|---|---|
| `nemo` (RTF 0.0006) | ~1,667 hours of audio |
| `faster-whisper` (RTF 0.03) | ~33 hours of audio |
| `onnx` (RTF 0.03) | ~33 hours of audio |
| `vllm-asr` (RTF 0.15) | ~6.7 hours of audio |
| `pyannote` (RTF 0.15) | ~6.7 hours of audio |

**Concurrent streams** — most engines declare `max_concurrency` in their
`engine.yaml` (typically 2–4). With concurrent streams, RTF degrades a bit
due to GPU contention, but throughput multiplies:

```
realistic_throughput = (1 / RTF) × max_concurrency × 0.7   # 0.7 = contention factor
```

So a single faster-whisper engine with `max_concurrency: 4` realistically
sustains ~90 audio-hours per wall-hour.

---

## Sizing worksheet

**Q: I have N audio-hours/day. What do I need?**

Step 1: pick an engine from [12-engine-presets-catalog.md](12-engine-presets-catalog.md).
Step 2: find its RTF.
Step 3: divide your daily audio by `(1 / RTF) × max_concurrency × 0.7`.

| Daily audio | Engine | Wall hours needed | Recommended setup |
|---|---|---|---|
| 1 hour | any | <1 | single engine on g4dn.xlarge spot, kill when done |
| 10 hours | faster-whisper | ~10 minutes | engine up + down |
| 100 hours | faster-whisper | ~1.5 hours | engine up + down, run mid-day |
| 100 hours | nemo (English) | ~5 minutes | engine up + down |
| 1,000 hours | nemo | ~30 minutes | engine up + down + repeat |
| 10,000 hours | nemo + 4 GPUs in parallel | ~75 minutes | split mode + 4 launched workers |
| 24/7 streaming | varies | always-on | split mode + on-demand for streaming workers |

---

## Cost per hour of audio

Combine RTF with hourly GPU cost.

```
cost_per_audio_hour = RTF × hourly_gpu_cost
```

For `faster-whisper` on a g4dn.xlarge spot (~$0.20/hr):

```
0.03 × $0.20 = $0.006 / audio hour
```

For `nemo` on the same hardware:

```
0.0006 × $0.20 = $0.00012 / audio hour
```

Compare to ElevenLabs Scribe (~$0.40/hr of audio) or OpenAI Whisper API
(~$0.36/hr). At even modest scale, self-hosted is dramatically cheaper.

But! The numbers above assume the GPU is doing nothing else — bill rounds
to the hour. If you only transcribe 5 minutes a day, the API price wins.
**Self-hosted economics kick in around 30+ audio-hours/month.**

Full breakdown with EBS / S3 / control-plane overhead:
[51-aws-cost-estimator.md](51-aws-cost-estimator.md).

---

## Cold start vs warm start

**Cold start** (first request after launch, model not yet loaded) =
warm-start latency + model download time:

| Engine | Cold start (cache miss) | Cold start (cache hit) |
|---|---|---|
| `onnx` | ~30s | <1s |
| `faster-whisper` | ~3 min | ~2s |
| `nemo` | ~3 min | ~3s |
| `vllm-asr` | ~5 min | ~6s |
| `pyannote-4.0` | ~30s | ~2s |

Strategies:

- **Pre-warm during boot.** The engine container loads the default model on
  startup, so by the time `/health` returns ok, you're warm.
- **Use the model cache.** Split mode persists `/data/models` on the
  control plane EBS — survives spot reclaim, survives `down`/`up` cycles.
- **Pre-stage models in S3.** If `DALSTON_MODEL_SOURCE=s3` and the model is
  in your bucket, the download is fast (no HF rate limits, no network
  latency to HF servers). See [30-how-models-are-fetched.md](30-how-models-are-fetched.md).

---

## Real-time latency budget

For streaming sessions, you care about **end-to-end time** from "I spoke a
word" to "the partial transcript appeared":

| Engine | Best case end-to-end | Why |
|---|---|---|
| `nemo` | ~100 ms | Native cache-aware streaming |
| `onnx` | ~150 ms | VAD-chunked, every ~100ms |
| `faster-whisper` | ~300 ms | Whisper isn't streaming-native; chunked |
| `vllm-asr` | ~500+ ms | LLM forward pass dominates |

For sub-200ms experiences, NeMo on a warm GPU is the answer. ElevenLabs
realtime targets a similar range; this is competitive.

---

## Concurrency & GPU memory

GPUs have fixed VRAM. The presets are tuned to fit common GPUs:

| GPU | VRAM | Co-location example |
|---|---|---|
| T4 (g4dn.xlarge) | 16 GB | one engine: faster-whisper, onnx, pyannote, hf-asr |
| L4 (g6.xlarge) | 24 GB | nemo (20 GB budget) + pyannote (4 GB budget) |
| A10G (g5.xlarge) | 24 GB | similar to L4; vllm-asr fits here |
| V100 (p3.2xlarge) | 16 GB | high-perf single engine |
| A100 (p4d) | 40–80 GB | multi-engine, lots of headroom |

The `nemo` and `pyannote` presets explicitly declare `DALSTON_VRAM_BUDGET_MB`
to enforce these splits at runtime — see
[`infra/scripts/dalston-aws:107`](../../infra/scripts/dalston-aws#L107) and `:124`.

`max_concurrency` is the per-engine concurrent-session ceiling (declared in
each `engine.yaml`). Beyond that, the gateway queues or rejects (depending
on policy).

---

## Bottleneck cheatsheet

| Symptom | Likely culprit |
|---|---|
| Pyannote dominates wall time | Co-locate with NeMo on a g6.xlarge, or add a second pyannote worker |
| Whisper-via-faster-whisper is slow | RTF 0.03 GPU is ~ as fast as it gets. Check you're really on GPU. Use NeMo if English. |
| First transcript takes 3 minutes | Cold start. See cache strategies above. |
| GPU 100% busy but throughput stalls | `max_concurrency` reached. Add a second worker. |
| Real-time partials lag by seconds | Wrong engine — Whisper isn't streaming-native. Use NeMo. |
| OOM mid-job | Model loaded with no VRAM budget; another engine ate the GPU. Check `DALSTON_VRAM_BUDGET_MB`. |

---

## Measuring it yourself

Every job emits timing telemetry. The Queue Board in the web console shows
per-stage wall time. For deeper analysis:

```bash
dalston jobs get <job_id> --json | jq '.timing'
```

For aggregate cost per episode (audio file), use the
`dalston-cost-correlate` tool — see [52-cost-correlate-tool.md](52-cost-correlate-tool.md).

---

## See also

- [12-engine-presets-catalog.md](12-engine-presets-catalog.md) — engine-by-engine RTF
- [51-aws-cost-estimator.md](51-aws-cost-estimator.md) — RTF × dollar = cost
- [52-cost-correlate-tool.md](52-cost-correlate-tool.md) — daily reports
- [30-how-models-are-fetched.md](30-how-models-are-fetched.md) — cold/warm start
- [`docs/specs/realtime/LATENCY_BUDGET_BACKPRESSURE.md`](../specs/realtime/LATENCY_BUDGET_BACKPRESSURE.md) — engineering reference for latency
