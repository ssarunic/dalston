# M98: Cohere Transcribe 2B Support

|                    |                                                              |
| ------------------ | ------------------------------------------------------------ |
| **Goal**           | Support CohereLabs cohere-transcribe-03-2026 as a high-accuracy multilingual batch model via the vllm-asr engine |
| **Duration**       | 2–3 days                                                     |
| **Dependencies**   | M89 (GPU-Aware VRAM Budgets), M97 (vllm-serve served mode)   |
| **Deliverable**    | Cohere family support via vllm-asr served mode, gated-model access, VRAM calibration profile |
| **Status**         | Not Started                                                  |

## User Story

> *"As an operator, I want a small, fast, Apache-licensed model with leaderboard-top multilingual accuracy, so that high-volume batch workloads get better WER per GPU-hour than Whisper-class models."*

---

## Research Findings (2026-08)

Model: [CohereLabs/cohere-transcribe-03-2026](https://huggingface.co/CohereLabs/cohere-transcribe-03-2026) ([release blog](https://huggingface.co/blog/CohereLabs/cohere-transcribe-03-2026-release))

- **What it is**: 2B encoder-decoder ASR model (>90% of params in the encoder), Apache 2.0. Took #1 on the Open ASR Leaderboard at 5.42% avg WER (March 2026; since edged out by Granite 4.1 / ARK-ASR / MOSS). 14 languages: English, German, French, Italian, Spanish, Portuguese, Greek, Dutch, Polish, Arabic, Vietnamese, Mandarin, Japanese, Korean. A dedicated Arabic variant exists ([cohere-transcribe-arabic-07-2026](https://huggingface.co/CohereLabs/cohere-transcribe-arabic-07-2026)).
- **Throughput is the headline**: ~3× offline throughput vs similarly sized ASR models; vLLM integration (built with the vLLM team) adds up to 2× more. This is a cost-per-audio-hour play, not just a WER play.
- **Serving — bigger than a request builder**: vLLM is the primary production path, with custom optimisations for variable-length audio into an encoder-decoder. Transformers / mlx / Rust / WebGPU ports exist. But the documented vLLM route is **`vllm serve` + the OpenAI-compatible `/v1/audio/transcriptions` HTTP endpoint, with `--trust-remote-code`** — while our vllm-asr inference path always calls `llm.chat` on an in-process `LLM` object. Chat-style prompting does not apply to this encoder-decoder, so supporting it requires runner/inference changes (a non-chat transcription path — most likely the served mode being introduced in M97), not just family detection in `batch_engine.py`.
- **Gated access prerequisite**: the CohereLabs weights are gated on Hugging Face — access must be requested/accepted for the account whose `HF_TOKEN` the containers use, and model-storage mirroring (M82) needs to work for a gated repo, before any of this can run.
- **No word timestamps, no streaming**: transcription text only. Align stage must supply word timing; RT mode only via VAD-chunked pseudo-streaming. Consistent with the engine's existing `word_timestamps: false`.
- **VRAM**: ~4–5 GB bf16 (<8 GB even quantised per Cohere). Smallest footprint of the new-model candidates; comfortable colocation headroom on L4, and the one model in this series that would also fit a T4 by capacity (bf16 caveat still applies on Turing).
- **Customisable punctuation** at inference time — optional knob to expose later.

### Recommendation

Best throughput-per-GPU story of the four new-model milestones, and worth doing as the "fast multilingual batch" option — but not the trivial config swap it first appears: it needs a non-chat serving path in vllm-asr. **Sequence it after M97**, whose `vllm serve` served mode provides exactly the `/v1/audio/transcriptions` endpoint this model documents; on top of that, this milestone shrinks back to family detection + trust-remote-code + calibration. Keep it in `vllm-asr` (shared image, shared admission control) rather than a new engine. Benchmark against faster-whisper large-v3 on our own audio before promoting to a default — leaderboard WER is within a point of everything else; throughput is the real decision variable.

---

## Steps

### 98.1: Gated-model access

**Deliverables:** HF access request accepted for the deployment `HF_TOKEN` account; gated repo verified to mirror through model storage (M82) into the container cache. Blocking prerequisite for everything below — do first.

---

### 98.2: Cohere family support in vllm-asr served mode

**Files modified:**

- `engines/stt-transcribe/vllm-asr/runner.py` — mark the `CohereLabs/cohere-transcribe-*` family as **served-mode** (M97): launch `vllm serve` with `--trust-remote-code`; batch requests go to the sidecar's `/v1/audio/transcriptions` endpoint (this family has no chat path — the embedded `llm.chat` route does not apply)
- `engines/stt-transcribe/vllm-asr/batch_engine.py` — family detection; build the transcription HTTP request; parse the response into `TranscriptionResult`
- `engines/stt-transcribe/vllm-asr/requirements.txt` — bump vLLM floor if cohere-transcribe support postdates the pinned build
- `engines/stt-transcribe/vllm-asr/engine.yaml` — document the new supported family and its served-mode + trust-remote-code requirement

**Deliverables:** batch transcription via `DALSTON_DEFAULT_MODEL=CohereLabs/cohere-transcribe-03-2026`; language forcing plumbed through if the endpoint exposes it.

---

### 98.3: Calibration + throughput benchmark

**Deliverables:**

- L4 VRAM profile calibrated (`sync_vram_presets`); expect ~6 GB working set — record coloc headroom
- Throughput benchmark vs faster-whisper large-v3 and parakeet-tdt-1.1b on the standard sample set: RTFx, WER (via existing eval tooling), cost per audio-hour on g6.xlarge spot; results recorded in this doc

---

## Non-Goals

- **Streaming support** — model is offline-only; pseudo-streaming adds no value over Parakeet's true streaming.
- **Word timestamps** — align stage responsibility.
- **hf-asr/transformers path** — vLLM is the optimised route; one integration is enough.
- **Arabic variant** — same family support makes it a config-only follow-up once the base model lands.
- **Punctuation customisation knob** — defer until a user asks.

---

## Verification

```bash
make dev-gpu
export DALSTON_DEFAULT_MODEL=CohereLabs/cohere-transcribe-03-2026

# Multilingual batch job completes with align-stage word timings
curl -s http://localhost:8000/v1/audio/transcriptions \
  -H "Authorization: Bearer $DALSTON_API_KEY" \
  -F file=@samples/german-1min.wav -F engine=vllm-asr | jq '.job_id'

# Capabilities remain honest
curl -s http://localhost:9100/v1/capabilities | jq '.supports_word_timestamps'  # false
```

---

## Checkpoint

- [ ] Gated HF access granted for deployment token; model mirrors through model storage
- [ ] vLLM version confirmed to support cohere-transcribe encoder-decoder serving
- [ ] Served mode launches with `--trust-remote-code`; batch requests flow via `/v1/audio/transcriptions`
- [ ] Batch job end-to-end in ≥3 languages
- [ ] L4 VRAM profile calibrated; coloc headroom documented
- [ ] Throughput/WER benchmark vs faster-whisper large-v3 recorded
- [ ] Decision recorded: promote as multilingual batch default or keep opt-in
