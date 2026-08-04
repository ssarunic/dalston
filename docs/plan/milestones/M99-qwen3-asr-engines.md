# M99: Qwen3-ASR Transcription + Forced-Aligner Engine

|                    |                                                              |
| ------------------ | ------------------------------------------------------------ |
| **Goal**           | Support Qwen3-ASR (1.7B/0.6B) for broad multilingual transcription via hf-asr, and Qwen3-ForcedAligner-0.6B as a new align-stage engine for multilingual word timestamps |
| **Duration**       | 3–5 days                                                     |
| **Dependencies**   | M89 (GPU-Aware VRAM Budgets)                                 |
| **Deliverable**    | Qwen3-ASR working through hf-asr, new `qwen3-aligner` align engine, VRAM profiles |
| **Status**         | Not Started                                                  |

## User Story

> *"As a user with non-European-language audio, I want accurate transcription across 52 languages/dialects with real word timestamps, so that languages Parakeet (25 EU languages) and Whisper underserve are first-class in Dalston."*

---

## Research Findings (2026-08)

Models: [Qwen/Qwen3-ASR-1.7B-hf](https://huggingface.co/Qwen/Qwen3-ASR-1.7B-hf), [Qwen/Qwen3-ASR-0.6B-hf](https://huggingface.co/Qwen/Qwen3-ASR-0.6B-hf), [Qwen/Qwen3-ForcedAligner-0.6B-hf](https://huggingface.co/Qwen/Qwen3-ForcedAligner-0.6B-hf) ([GitHub](https://github.com/QwenLM/Qwen3-ASR))

- **Coverage is the differentiator**: 52 languages/dialects (30 languages + 22 Chinese dialects), language identification, robust to music/song. 5.76% avg WER (1.7B) — within a point of the leaderboard top. Apache 2.0. Released Jan 2026.
- **Native transformers support**: the `-hf` model variants load with standard `AutoProcessor`/`AutoModel` classes and support `torch.compile`. Our transformers pin (`>=5.13.1`) is recent enough. **Open question to verify first**: whether the `-hf` variants register with `pipeline("automatic-speech-recognition")` — the hf-asr engine is pipeline-based. If not, the hf-asr model manager needs a small non-pipeline adapter (processor + model.generate) behind the same interface.
- **Timestamps are a two-model story**: the ASR model emits text; word timestamps come from the separate **Qwen3-ForcedAligner-0.6B**, which timestamps arbitrary units within up to 5 minutes of audio in 11 languages (zh, en, yue, fr, de, it, ja, ko, pt, ru, es), and claims better accuracy than E2E forced-alignment models. It runs a **single forward pass** (no autoregressive decoding — `Qwen3ASRForTokenClassification` in transformers), making it cheap and torch.compile-friendly. This is architecturally a perfect fit for Dalston's **align stage** — not something to bolt into the transcribe engine.
- **hf-asr wrinkle**: `batch_engine.py` unconditionally requests `return_timestamps="word"` from the pipeline. Qwen3-ASR won't honour that; the engine needs graceful per-model fallback instead of erroring.
- **Timestamp capability must be known at DAG-planning time, not runtime**: the orchestrator decides whether the align stage is in the task DAG **before** transcription runs, from the engine-level `supports_word_timestamps` capability — and hf-asr advertises `word_timestamps: true`. Reporting `word_timestamps: false` in the transcription result is too late to add an align task. Supporting Qwen3-ASR therefore requires **model-aware capability resolution during planning**: when a job pins a `loaded_model_id`, the planner consults a per-model capability record (model registry / engine catalog) that overrides the engine-level flag. (Alternative: register Qwen3 models under a separate engine identity with truthful flags, as M96 does for SALM — more deployment surface, less orchestrator change.)
- **VRAM**: tiny. 1.7B bf16 ≈ 3.5 GB (+activations ≈ 5 GB); aligner ≈ 1.5 GB. Both colocate easily on L4 under existing budgets; 0.6B ASR variant is a candidate for CPU or T4 fallback tiers.
- **Aligner as a general asset**: the aligner takes (audio, text) — it can align **any** engine's transcript in its 11 languages, not just Qwen3-ASR output. That makes it an alternative/complement to the existing align engine for multilingual jobs.

### Recommendation

Best all-round candidate of the four: Apache 2.0, widest language coverage, smallest VRAM, and the only one that brings its own (superior) alignment answer. Do the aligner as a proper align-stage engine rather than a transcribe-engine internal — it then benefits every pipeline, including Cohere Transcribe (M98) and Canary-Qwen (M96) which produce no timestamps. The hidden cost is orchestrator work, not model integration: planning-time model-aware capabilities (99.2) and language-aware align selection (99.5) are the two pieces with no existing mechanism. Suggested order: verify pipeline compat (hours), then planner capability work, then aligner engine, then wire the ASR model in.

---

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│              MULTILINGUAL PIPELINE (after M99)               │
│                                                              │
│  TRANSCRIBE                    ALIGN                         │
│  ┌─────────────────┐          ┌──────────────────────┐       │
│  │ hf-asr           │  text   │ qwen3-aligner        │       │
│  │ (Qwen3-ASR-1.7B) │ ───────▶│ (ForcedAligner-0.6B) │──▶ …  │
│  └─────────────────┘          │  word timestamps,    │       │
│  also: vllm-asr, nemo-salm ──▶│  11 languages,       │       │
│  (any timestamp-less engine)  │  ≤5 min windows      │       │
│                               └──────────────────────┘       │
└──────────────────────────────────────────────────────────────┘
```

---

## Steps

### 99.1: Verify pipeline compatibility (spike)

**Deliverables:** confirm whether `Qwen3-ASR-*-hf` works via `transformers.pipeline("automatic-speech-recognition")` in the hf-asr container. Outcome decides 99.2's shape. Also confirm the `return_timestamps="word"` failure mode.

---

### 99.2: Model-aware timestamp capability at DAG planning

**Files modified:**

- Model registry / engine catalog — per-model capability record: `Qwen/Qwen3-ASR-*-hf` → `word_timestamps: false` (schema addition, ask before touching registry data per project convention)
- Orchestrator DAG planner — when a job pins `loaded_model_id`, resolve `supports_word_timestamps` from the per-model record before falling back to the engine-level flag; align stage is added to the DAG accordingly
- Orchestrator tests — DAG for (hf-asr + whisper) contains no align stage; DAG for (hf-asr + Qwen3-ASR) contains align

**Deliverables:** align stage is planned **up front** for timestamp-less models; engine-level capabilities stay truthful for the default model.

---

### 99.3: Qwen3-ASR through hf-asr

**Files modified:**

- `engines/stt-transcribe/hf-asr/batch_engine.py` — skip `return_timestamps="word"` for models flagged timestamp-less (same capability record as 99.2) instead of erroring; surface `word_timestamps: false` in result metadata for observability
- hf-asr model manager — non-pipeline adapter (AutoProcessor + generate) *only if* the 99.1 spike shows pipeline incompat

**Deliverables:** batch transcription with `loaded_model_id=Qwen/Qwen3-ASR-1.7B-hf`; language identification surfaced in result metadata where available.

---

### 99.4: qwen3-aligner align engine

**Files modified:**

- `engines/stt-align/qwen3-aligner/` *(new)* — `Dockerfile` (on `base-pytorch`), `engine.yaml`, `requirements.txt`, `engine.py` using `Qwen3ASRForTokenClassification`

**Deliverables:**

- Align-stage engine consuming (audio, transcript) → word-level timestamps; chunks inputs >5 min at transcript boundaries
- torch.compile enabled (single forward pass — the ideal case)

---

### 99.5: Align-stage language-aware selection

No existing mechanism expresses "prefer qwen3-aligner for its languages": `EngineCapabilities` has no language list, `engine.yaml` cannot state a routing preference, and the selector consults model-registry languages only *after* ranking engines. This step adds the mechanism explicitly rather than assuming it.

**Files modified:**

- `dalston/common/` engine capability schema — add supported-languages metadata to align-stage capabilities (`EngineCapabilities`), populated from `engine.yaml`
- Orchestrator engine selector — explicit align-stage priority rule: when the job's detected/declared language is in an align engine's supported set, rank it above language-agnostic aligners (qwen3-aligner over phoneme-align for its 11 languages); fall back to the existing aligner otherwise
- Selector tests — Japanese job → qwen3-aligner; unsupported language (e.g. Vietnamese) → existing aligner; language-unknown → existing aligner

**Deliverables:** deterministic, tested align-engine selection; L4 VRAM profiles for both models via `sync_vram_presets`.

---

## Non-Goals

- **vLLM serving of Qwen3-ASR** — transformers path is sufficient at this size; revisit if throughput demands it.
- **Realtime/streaming** — offline models; Parakeet and Voxtral Realtime (M97) own streaming.
- **Chinese-dialect eval** — coverage claims accepted at face value initially; targeted eval when a user needs it.
- **Replacing the existing align engine wholesale** — qwen3-aligner is additive for its 11 languages; existing alignment stays the fallback.
- **0.6B ASR variant rollout** — same code path; enable later as a cheap tier if wanted.

---

## Verification

```bash
make dev-gpu

# Japanese audio: transcribe with Qwen3-ASR, expect word timestamps from qwen3-aligner
curl -s http://localhost:8000/v1/audio/transcriptions \
  -H "Authorization: Bearer $DALSTON_API_KEY" \
  -F file=@samples/japanese-1min.wav \
  -F engine=hf-asr -F model=Qwen/Qwen3-ASR-1.7B-hf | jq '.job_id'

# Result contains word-level timestamps and aligner attribution
curl -s http://localhost:8000/v1/audio/transcriptions/$JOB_ID \
  -H "Authorization: Bearer $DALSTON_API_KEY" | jq '.words[:3]'

# Aligner also aligns another engine's transcript (cross-engine use)
# → submit same file with engine=vllm-asr and confirm align stage ran qwen3-aligner
```

---

## Checkpoint

- [ ] Pipeline-compat spike documented (pipeline vs adapter path)
- [ ] Per-model capability record consulted at DAG planning; align stage planned up front for Qwen3-ASR jobs (with orchestrator tests)
- [ ] Qwen3-ASR-1.7B transcribes via hf-asr without timestamp-request errors
- [ ] `qwen3-aligner` align engine produces word timestamps for its 11 languages
- [ ] >5 min audio handled by aligner chunking
- [ ] Align-stage language metadata + selector priority rule, with routing tests (supported / unsupported / unknown language)
- [ ] L4 VRAM profiles calibrated for both models
