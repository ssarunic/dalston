# M96: Canary-Qwen 2.5B SALM Engine

|                    |                                                              |
| ------------------ | ------------------------------------------------------------ |
| **Goal**           | Support NVIDIA Canary-Qwen 2.5B (SALM architecture) as a NeMo-based transcribe engine for top-accuracy English batch transcription |
| **Duration**       | 2–3 days                                                     |
| **Dependencies**   | M86 (Shared VAD Chunking), M89 (GPU-Aware VRAM Budgets)      |
| **Deliverable**    | New `nemo-salm` engine on `base-nemo`, SALM loading + generate-based inference path in engine SDK, VRAM calibration profile |
| **Status**         | Not Started                                                  |

## User Story

> *"As an operator, I want to offer leaderboard-top English transcription accuracy (5.63% avg WER) without adding a new framework stack, so that quality-sensitive jobs get better results on the hardware and base images we already run."*

---

## Research Findings (2026-08)

Model: [nvidia/canary-qwen-2.5b](https://huggingface.co/nvidia/canary-qwen-2.5b)

- **Architecture**: SALM (Speech-Augmented Language Model) — FastConformer encoder + unmodified Qwen3-1.7B decoder, 2.5B params total. Lives in `nemo.collections.speechlm2.models.SALM`, **not** the `nemo.collections.asr` collection our loader uses.
- **Inference API**: prompt-based `model.generate(prompts=[[{"role": "user", "content": f"Transcribe: {model.audio_locator_tag}", "audio": [...]}]])` returning token IDs. There is **no** `.transcribe()` method, so `NemoInference.transcribe_batch_with_model()` (which calls `model.transcribe(..., return_hypotheses=True)`) cannot drive it.
- **No word timestamps**: SALM emits text only. Jobs must route through the align stage for word timing.
- **No streaming**: no cache-aware streaming support. Realtime mode is only possible via VAD-chunked pseudo-streaming (the collect-then-transcribe fallback already used for non-cache-aware NeMo models).
- **Audio cap**: max training duration was 40 s; quality degrades beyond that. Maps directly onto the existing M86 `get_max_audio_duration_s()` chunking hook.
- **NeMo version**: requires ≥2.5.0 — our pin `nemo_toolkit[asr]>=2.7.3,<2.8.0` already satisfies it. However, the model card installs `nemo_toolkit[asr,tts]`; the `speechlm2` collection pulls deps outside the `[asr]` extra (transformers for the Qwen decoder, etc.). `Dockerfile.base-nemo` needs its extras widened.
- **Precision / hardware**: published in **bf16**; NVIDIA tested A6000 / A100 / RTX 5090 only. Ampere+ required for native bf16.
  - **L4 (g6.xlarge, 24 GB)**: correct target. ~5 GB weights + ~3 GB activations/KV ≈ 8 GB working set; ample headroom under calibrated budgets.
  - **T4 (g4dn.xlarge, 16 GB)**: fits by capacity but Turing has no native bf16. fp16 risks decoder overflow (Qwen trained in bf16); fp32 doubles weights to ~10 GB and is slow. **Not recommended.**
- **Speed**: RTFx 418 on A100; expect a healthy multiple of realtime on L4.
- **Language/license**: English-only. CC-BY-4.0 (attribution required — same as Parakeet, already handled).

### Recommendation

Ship as a **separate engine directory** (`engines/stt-transcribe/nemo-salm/`) sharing `base-nemo`, rather than adding to the unified `nemo` engine's `SUPPORTED_MODELS`. Rationale: the nemo engine advertises `word_timestamps: true` and cache-aware streaming — both false for SALM. Per-engine capability flags stay truthful without introducing per-model capability plumbing. The engine remains unified (batch + RT adapters); RT uses pseudo-streaming with honest latency characteristics.

---

## Steps

### 96.1: SALM support in engine SDK

**Files modified:**

- `dalston/engine_sdk/managers/nemo.py` — add `"salm"` architecture to `get_architecture()` / `ARCHITECTURE_LOADERS`; `_load_model()` branch importing `nemo.collections.speechlm2.models.SALM` (the existing `getattr(nemo_asr.models, ...)` path cannot resolve it)
- `dalston/engine_sdk/inference/nemo_inference.py` — new `generate_transcript_with_model()` path: builds the audio-locator prompt, calls `model.generate()`, detokenizes via `model.tokenizer.ids_to_text()`, returns `NeMoTranscriptionResult` with segments but **no word entries**

**Deliverables:** SALM models load through `NeMoModelManager` and produce plain-text transcription results through `NemoInference`.

---

### 96.2: base-nemo image extras

**Files modified:**

- `docker/Dockerfile.base-nemo` — widen `nemo_toolkit[asr]` → `nemo_toolkit[asr,tts]` (speechlm2 deps)
- `engines/stt-transcribe/nemo/requirements.txt` — mirror the extras change

**Deliverables:** `from nemo.collections.speechlm2.models import SALM` succeeds inside the container. Rebuild base + dependent engines per the standard staleness procedure.

---

### 96.3: nemo-salm engine

**Files modified:**

- `engines/stt-transcribe/nemo-salm/` *(new)* — `Dockerfile`, `engine.yaml`, `requirements.txt`, `batch_engine.py`, `rt_engine.py`, `runner.py` (modelled on `engines/stt-transcribe/nemo/`)

**Deliverables:**

- `engine.yaml`: `word_timestamps: false`; `min_vram_gb: 10`; `recommended_gpu: [l4, a10g]`; English-only
- **Streaming capability follows the existing pseudo-streaming catalog convention** (as hf-asr does): `native_streaming: true` backed by VAD-chunked pseudo-streaming. Dalston hard-filters streaming requests when `supports_native_streaming` is false, so declaring `false` would make the RT adapter unroutable. The description documents the real latency characteristics (chunk-granularity partials, not ~100ms cache-aware streaming)
- `get_max_audio_duration_s()` returns 40 s (env-overridable `DALSTON_SALM_MAX_CHUNK_S`) → M86 VAD chunking handles long files
- Batch adapter drives the new SALM inference path; RT adapter uses VAD-chunked pseudo-streaming
- docker-compose service definition (GPU profile only — `supports_cpu: false`)

---

### 96.4: VRAM calibration + fleet config

**Files modified:**

- L4 VRAM preset for `nemo-salm` via the standard calibration flow (`sync_vram_presets`)

**Deliverables:** calibrated budget on g6.xlarge; verified colocation headroom against existing engine budgets.

---

## Non-Goals

- **T4 / g4dn support** — no native bf16 on Turing; untested precision fallbacks. Revisit only if fp16 output is validated.
- **Native word timestamps from SALM** — not supported by the model; align stage covers it.
- **True streaming** — no cache-aware decoder; pseudo-streaming only.
- **LLM-mode prompting (summarisation, Q&A over audio)** — SALM can do it; out of scope for the transcribe stage.
- **Adding SALM to the unified `nemo` engine** — capability flags would become per-model lies; separate engine keeps them truthful.

---

## Verification

```bash
make dev-gpu

# Submit an English file targeting the new engine; expect transcript with align-stage word timings
curl -s http://localhost:8000/v1/audio/transcriptions \
  -H "Authorization: Bearer $DALSTON_API_KEY" \
  -F file=@samples/english-2min.wav \
  -F engine=nemo-salm | jq '.job_id'

# >40s file should produce an outer engine.chunked_request span wrapping
# per-chunk engine.recognize spans (M86 chunking)
make logs ENGINE=nemo-salm | grep chunked_request

# Capability endpoint reports no native word timestamps
curl -s http://localhost:9100/v1/capabilities | jq '.supports_word_timestamps'  # false
```

---

## Checkpoint

- [ ] SALM loads via `NeMoModelManager` with `speechlm2` branch
- [ ] `generate`-based inference path returns transcripts through `NemoInference`
- [ ] base-nemo rebuilt with `[asr,tts]` extras; existing nemo engines unaffected
- [ ] `nemo-salm` engine passes a batch job end-to-end with align-stage timestamps
- [ ] >40 s audio auto-chunks via M86 hook
- [ ] L4 VRAM profile calibrated and synced
- [ ] CC-BY-4.0 attribution noted in engine docs
