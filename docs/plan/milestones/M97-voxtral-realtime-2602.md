# M97: Voxtral Mini 4B Realtime 2602

|                    |                                                              |
| ------------------ | ------------------------------------------------------------ |
| **Goal**           | Support Mistral's Voxtral Mini 4B Realtime 2602 in the vllm-asr engine, including true low-latency streaming via vLLM's Realtime API |
| **Duration**       | 2–4 days                                                     |
| **Dependencies**   | M89 (GPU-Aware VRAM Budgets)                                 |
| **Deliverable**    | Model swap for batch mode, vLLM Realtime API streaming adapter, VRAM calibration profile |
| **Status**         | Not Started                                                  |

## User Story

> *"As a realtime API consumer, I want sub-500ms multilingual streaming transcription with offline-grade accuracy, so that live subtitling and voice-assistant use cases don't have to trade latency for quality."*

---

## Research Findings (2026-08)

Model: [mistralai/Voxtral-Mini-4B-Realtime-2602](https://huggingface.co/mistralai/Voxtral-Mini-4B-Realtime-2602)

- **What it is**: streaming-native successor to Voxtral Mini 3B (our current `vllm-asr` default). 4B params (3.4B LM + 970M audio encoder), 13 languages, Apache 2.0. First open model claiming offline-grade accuracy at <500 ms delay; delay is **configurable 80 ms–2400 ms** (accuracy/latency trade-off at session setup).
- **vLLM-only**: due to the novel streaming architecture, inference is currently supported **only in vLLM** — no transformers path. Fits our `vllm-asr` engine and nothing else.
- **Two integration levels**:
  1. **Batch (config-mostly)**: the existing offline `LLM` path in `runner.py` should accept it via `DALSTON_DEFAULT_MODEL` / model allow-list, same as Voxtral 3B. Long-file handling continues through VAD chunking. One required change: the runner forces `max_model_len=4096`; at Voxtral's ~80 ms of audio per text token that truncates sessions at roughly **5.5 minutes**. Upstream recommends the model default (**131072**) for multi-hour use — the runner needs a per-model context-length default.
  2. **True streaming (architectural change, not an adapter tweak)**: the Realtime API (`/v1/realtime` WebSocket) is served by **`vllm serve`**, not by the in-process `LLM` object our runner embeds. `rt_engine.py` alone cannot open a realtime session against the current architecture. Supporting this model means running a vLLM **server** for it — see 97.2 for the serving design (lifecycle, health, auth, admission).
- **Delay is a deployment-time setting, not per-session**: the transcription delay is configured in the model's tokenizer config (`tekken.json`), i.e. fixed per loaded model instance. vLLM's `session.update` protocol selects the model but does not renegotiate delay. Offering multiple delay profiles means multiple deployments (or multiple tokenizer instances), not a session knob.
- **vLLM version**: our pin is `vllm[audio]>=0.25.1`. Verify the installed version in the container actually ships the Realtime API + Voxtral Realtime support (production support landed early 2026; a floor bump may be needed). See the [vLLM recipe](https://recipes.vllm.ai/mistralai/Voxtral-Mini-4B-Realtime-2602) and [vLLM speech-to-text serving docs](https://docs.vllm.ai/en/latest/serving/online_serving/speech_to_text/).
- **VRAM**: bf16 weights ≈ 9 GB; model card says a single GPU with **≥16 GB**. On L4 (24 GB) it runs solo with modest headroom — colocation with other GPU engines is unlikely to fit under current budgets. T4 is out (capacity + no native bf16).
- **No word timestamps** — consistent with the engine's existing `word_timestamps: false`; hybrid mode (RT + batch enhancement) covers timing/diarization as today.

### Recommendation

Do this in two independently shippable slices: batch model swap first (cheap, validates the model on our hardware), then the realtime serving path. The realtime path is the valuable part — it's what differentiates this model from the Voxtral 3B we already run — but it is also a real architectural change: the engine moves from an embedded `LLM` object to managing a `vllm serve` process. Prioritise after M96 unless realtime latency is the pressing need; if it is, this milestone matters more than any accuracy-motivated one.

---

## Architecture

The engine has two mutually exclusive serving modes, selected by the loaded model. Realtime-native models require a `vllm serve` process (only it exposes `/v1/realtime`); embedded-LLM models keep today's in-process path. One model in VRAM either way.

```
┌──────────────────────────────────────────────────────────────────┐
│                   VLLM-ASR SERVING MODES (after M97)             │
│                                                                  │
│  Client ──WS──▶ Gateway ──WS──▶ rt_engine.py (admission gate)    │
│                                    │                             │
│              ┌─────────────────────┴────────────────┐            │
│              │ mode: embedded            mode: served │           │
│              ▼                                       ▼           │
│   in-process vLLM LLM object          vllm serve subprocess      │
│   VAD-chunked pseudo-stream           (localhost-only, API key)  │
│   (Voxtral 3B, Qwen2-Audio)             │                        │
│                                         ├─ /v1/realtime ◀── WS   │
│                                         │  (streaming sessions)  │
│                                         └─ HTTP audio API ◀──    │
│                                            (batch requests)      │
└──────────────────────────────────────────────────────────────────┘
```

---

## Steps

### 97.1: Batch model swap + context-length fix + admission profile

**Files modified:**

- `engines/stt-transcribe/vllm-asr/runner.py` — add `mistralai/Voxtral-Mini-4B-Realtime-2602` to the supported-family handling (prompt format is the Voxtral family's); replace the hardcoded `max_model_len=4096` default with a per-model default (**131072** for this model, per upstream guidance — 4096 truncates at ~5.5 minutes of audio at ~80 ms/token), still overridable via `DALSTON_VLLM_MAX_MODEL_LEN`
- `engines/stt-transcribe/vllm-asr/batch_engine.py` — family handling as above
- `engines/stt-transcribe/vllm-asr/requirements.txt` — bump vLLM floor if the pinned build predates Voxtral Realtime support

**Deliverables:** batch jobs run against the new model via `DALSTON_DEFAULT_MODEL`; a **>6-minute** audio file transcribes without truncation; admission params resolved from a calibration profile (`_resolve_admission_params`).

---

### 97.2: vllm-serve mode + Realtime streaming adapter

**Files modified:**

- `engines/stt-transcribe/vllm-asr/runner.py` — new **served mode**: for realtime-native models, launch and supervise a `vllm serve` subprocess instead of constructing an in-process `LLM`. Lifecycle: start on model load, readiness-gate the engine's `/health` on the server's health endpoint, terminate on shutdown/model swap. Security: bind localhost-only with a generated API key. Admission: the existing admission gate wraps session/request intake **before** anything reaches the vLLM server, preserving shared admission behaviour across both modes
- `engines/stt-transcribe/vllm-asr/rt_engine.py` — realtime adapter: open a WebSocket to the sidecar's `/v1/realtime`, forward PCM frames, relay incremental transcript events to the Dalston RT protocol
- `engines/stt-transcribe/vllm-asr/batch_engine.py` — in served mode, route batch requests through the sidecar's HTTP audio API so batch and RT share the single loaded model
- `engines/stt-transcribe/vllm-asr/engine.yaml` — document served mode and the delay profile

**Deliverables:**

- End-to-end streaming session with measured first-partial latency
- **Delay is per-deployment, not per-session**: configured via the model's tokenizer config (`tekken.json`) at load time, exposed as `DALSTON_VLLM_RT_DELAY_MS` (default 480) applied when the served model is prepared. Multiple delay profiles = multiple engine deployments; document this in the engine description

---

### 97.3: VRAM calibration + fleet config

**Deliverables:** L4 profile calibrated (expect ~10–12 GB working set incl. KV cache); autoscaler/coloc budgets updated to treat this model as effectively solo-tenant on g6.xlarge.

---

## Non-Goals

- **Word timestamps** — model doesn't produce them; hybrid enhancement path covers it.
- **Transformers/hf-asr support** — vLLM-only architecture upstream.
- **T4 support** — 16 GB capacity floor plus no native bf16.
- **Replacing the Voxtral 3B default** — keep 3B as default until 2602 accuracy/latency is validated in prod.

---

## Verification

```bash
make dev-gpu
export DALSTON_DEFAULT_MODEL=mistralai/Voxtral-Mini-4B-Realtime-2602

# Batch path
curl -s http://localhost:8000/v1/audio/transcriptions \
  -H "Authorization: Bearer $DALSTON_API_KEY" \
  -F file=@samples/french-1min.wav -F engine=vllm-asr | jq '.job_id'

# Context-length regression: >6 min file must transcribe to the end
# (catches the old max_model_len=4096 ~5.5-minute truncation)
curl -s http://localhost:8000/v1/audio/transcriptions \
  -H "Authorization: Bearer $DALSTON_API_KEY" \
  -F file=@samples/english-8min.wav -F engine=vllm-asr | jq '.job_id'

# Realtime path: stream a file, expect first partial < 1s and steady incremental output
python scripts/rt_smoke.py --engine vllm-asr --file samples/english-8min.wav --report-latency
```

---

## Checkpoint

- [ ] vLLM version in container confirmed to support Voxtral Realtime + Realtime API
- [ ] Batch transcription works with the 2602 model (13 languages spot-checked on 2–3)
- [ ] Per-model `max_model_len` default (131072 for 2602); >6-minute file transcribes without truncation
- [ ] Served mode: `vllm serve` subprocess supervised by the runner (health-gated startup, clean shutdown, localhost + API key)
- [ ] Admission gate wraps both modes; batch and RT share one loaded model in served mode
- [ ] RT adapter streams via the sidecar's `/v1/realtime`; first-partial latency measured and documented
- [ ] Delay profile (80–2400 ms) configurable **per deployment** via tokenizer config; limitation documented
- [ ] L4 VRAM profile calibrated; coloc policy updated
- [ ] Voxtral 3B remains default; 2602 selectable per job/session
