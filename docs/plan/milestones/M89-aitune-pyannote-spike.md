# M89: AITune Spike on Pyannote Diarization

|                    |                                                              |
| ------------------ | ------------------------------------------------------------ |
| **Goal**           | Time-boxed benchmark of NVIDIA AITune on the pyannote-4.0 engine to decide whether to adopt it for PyTorch-native engines. |
| **Duration**       | 2–3 days                                                     |
| **Dependencies**   | M84 (VRAM Budget Diarize Chunking)                           |
| **Deliverable**    | Benchmark report, go/no-go decision, throwaway spike branch  |
| **Status**         | Not Started                                                  |

## User Story

> *"As a Dalston operator running pyannote on GPU, I want to know whether AITune's TensorRT compilation improves diarization throughput enough to justify adopting it, so that I can either roll it out or close the door and stop re-evaluating."*

---

## Motivation

AITune (`github.com/ai-dynamo/aitune`) is NVIDIA's new single-line PyTorch inference optimizer with TensorRT, Torch-TensorRT, TorchAO, and Inductor backends. Most Dalston hot-path engines already have specialized acceleration:

- `faster-whisper` → CTranslate2
- `nemo*`, `riva` → native TensorRT export
- `onnx` → ONNX Runtime
- `vllm-asr` → vLLM runtime

The only engines where AITune plausibly moves the needle are the PyTorch-native ones: `pyannote-4.0`, `phoneme-align` (wav2vec2), and `hf-asr`. Pyannote is the best spike target because (a) it is a known VRAM bottleneck that already required chunking in M84, (b) the Community-1 pipeline contains several sub-modules AITune could auto-detect, and (c) it runs on every hybrid-mode job.

If AITune gives <15% throughput improvement we drop it permanently. If it gives ≥30% or reduces VRAM enough to raise `DALSTON_MAX_DIARIZE_CHUNK_S`, a follow-up milestone productionizes it.

---

## Architecture

No production architecture change in this milestone — the spike runs in a throwaway branch and produces measurements only.

```
┌────────────────────────────────────────────────────────────┐
│              SPIKE SETUP (throwaway)                       │
│                                                            │
│   pyannote Pipeline ──▶ aitune.tune() ──▶ Tuned Pipeline  │
│         │                                      │           │
│         ▼                                      ▼           │
│   baseline bench                        tuned bench        │
│   (ms/audio-s, VRAM peak, WDER drift)                      │
└────────────────────────────────────────────────────────────┘
```

---

## Steps

### 89.1: Standalone benchmark harness

**Files modified:**

- `engines/stt-diarize/pyannote-4.0/spike_aitune.py` *(new, throwaway)*

**Deliverables:**

A standalone script that loads the pyannote Community-1 pipeline, runs it over the corpus defined below, and records:

- Wall-clock ms per audio-second
- Peak CUDA memory via `torch.cuda.max_memory_allocated()`
- Output diarization (for DER drift check against baseline)

Run order: baseline → `aitune.tune(pipeline)` on applicable sub-modules (segmentation, embedding) → tuned. Include Torch Inductor and Torch-TensorRT backends separately — skip TensorRT standalone for the spike (pipeline is not a single graph).

```python
# sketch
import aitune
from pyannote.audio import Pipeline

pipeline = Pipeline.from_pretrained("pyannote/speaker-diarization-community-1", token=HF_TOKEN)
pipeline.to(torch.device("cuda"))

# Baseline timings
baseline = bench(pipeline, corpus)

# Tune — try both auto-detected modules and explicit segmentation/embedding
tuned_pipeline = aitune.tune(pipeline, backend="torch_tensorrt")
tuned = bench(tuned_pipeline, corpus)
```

---

**Corpus:**

| Tier | Source | Files | Length | Purpose |
| ---- | ------ | ----- | ------ | ------- |
| Smoke | `tests/audio/test_stereo_speakers.wav`, `tests/audio/test_merged.wav` | 2 | <1min | Pipeline wiring / sanity check; too short for throughput numbers |
| Primary | AMI Meeting Corpus headset-mix, `test` split (ES2004a, ES2014a, IS1009a, TS3003a, TS3007a) | 5 | ~20–30min each | Main throughput + DER measurement; same split pyannote uses for its published scores, so baseline DER ≈ 22% is a known anchor |
| Long-tail stress | AMI `ES2004b` full meeting | 1 | ~40min | Exercises chunked path + VRAM ceiling |

For dev-loop iteration, pass `--first-seconds 300` to crop AMI files to their first 5 minutes. Full-length is only required for the final results table. AMI is CC-BY 4.0 — download script sketch:

```bash
# tests/audio/ami/ is gitignored; fetched on the GPU host
scripts/fetch_ami_spike.sh  # wgets 6 .wav files (~300MB total) to tests/audio/ami/
```

We deliberately avoid VoxConverse (licence requires per-user registration, awkward for a CI-adjacent benchmark) and DIHARD (gated). If AMI throughput improves but we want a second dataset before adopting, add one in the follow-up milestone, not here.

---

### 89.2: Correctness check

**Files modified:**

- `engines/stt-diarize/pyannote-4.0/spike_aitune.py`

**Deliverables:**

Compare diarization output before/after tuning on each corpus file. AMI ships reference RTTMs, so compute absolute DER via `pyannote.metrics` rather than just pre/post drift. Accept if DER drift ≤ 0.5 absolute on the aggregate. Reject outright if AITune changes speaker count on any file — the VBx clustering head is numerically sensitive and a silent regression there would not surface until customers complain.

---

### 89.3: Decision memo

**Files modified:**

- `docs/plan/milestones/M89-aitune-pyannote-spike.md` — append Results section

**Deliverables:**

A short results table (baseline vs. each backend: ms/audio-s, peak VRAM, DER drift) and a one-paragraph recommendation:

- **Adopt** (≥30% throughput or meaningful VRAM reduction): open M90 to integrate AITune into the engine lifecycle (load-time tune + cache)
- **Shelve** (15–30%): park; revisit when AITune hits 1.0 or when we next pick up `hf-asr` or `phoneme-align`
- **Reject** (<15% or correctness drift): document and close

---

## Non-Goals

- **Productionizing AITune in the engine** — this spike is measurement only; adoption is a separate milestone.
- **Other engines (`hf-asr`, `phoneme-align`)** — if pyannote wins, they come next; if it loses, we don't bother.
- **TensorRT standalone backend** — pyannote is a multi-module pipeline, not a single export-able graph. Torch-TensorRT is the right backend here.
- **CPU/MPS paths** — AITune is NVIDIA-GPU only; spike runs on a `make dev-gpu` / AWS GPU host.
- **Chunking rework** — M84's chunked path stays unchanged even if VRAM improves; raising `DALSTON_MAX_DIARIZE_CHUNK_S` is a config-only follow-up.

---

## Verification

```bash
# Run on GPU host (local dev-gpu or AWS)
export HF_TOKEN=hf_xxx
cd engines/stt-diarize/pyannote-4.0
pip install aitune  # plus TensorRT 10.5+, PyTorch 2.7+
scripts/fetch_ami_spike.sh            # populates tests/audio/ami/
python spike_aitune.py \
  --corpus ../../../tests/audio/ami \
  --first-seconds 300 \
  --out results.json

# Expect a results.json with three rows (baseline, inductor, torch_tensorrt)
# and a DER-drift column per backend.
jq '.[] | {backend, ms_per_audio_s, peak_vram_gb, der_drift}' results.json
```

---

## Checkpoint

- [ ] Spike script runs baseline + at least one AITune backend on GPU host
- [ ] Results table filled in this milestone doc
- [ ] DER drift measured on all corpus files
- [ ] Go/no-go recommendation recorded
- [ ] Spike branch closed (merged as doc-only, or abandoned)
