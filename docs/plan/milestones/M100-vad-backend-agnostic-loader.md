# M100: Backend-Agnostic Silero VAD Loader

|                    |                                                              |
| ------------------ | ------------------------------------------------------------ |
| **Goal**           | Let realtime VAD run on whichever inference runtime an image already ships — torch JIT on torch images, ONNX on ONNX images — instead of hard-requiring `onnxruntime` everywhere |
| **Duration**       | 1–2 days                                                     |
| **Dependencies**   | M86 (Shared VAD Chunking)                                    |
| **Deliverable**    | `SileroTorchModel` wrapper, `load_silero_model()` two-tier loader in `engine_sdk/silero_vad.py`, `realtime_sdk/vad.py` switched to it, `onnxruntime` removed from `base-nemo`, unit tests |
| **Status**         | Not Started                                                  |

## User Story

> *"As an operator, I want realtime transcription to work on any GPU engine image without installing a second inference runtime, so that adding a realtime engine doesn't silently break on images that ship PyTorch but not onnxruntime."*

---

## Outcomes

| Scenario | Current | After M100 |
| -------- | ------- | ---------- |
| Realtime session on a NeMo worker | Dies ~1s after connect: `onnxruntime is required for Silero VAD but not installed` (fixed in PR #349 by adding onnxruntime) | Works using the torch JIT model already bundled in the `silero-vad` wheel |
| Realtime engine added to `base-pytorch` or `base-pyannote` | Fails identically — neither image ships `onnxruntime` | Works, once each image installs the `silero-vad` package (see 100.5) |
| Realtime engine on `base-onnx` / `base-engine` | Works via onnxruntime | Unchanged — still uses onnxruntime |
| `base-nemo` image contents | Carries `onnxruntime-gpu` (~200–400 MB CUDA runtime) solely for VAD, plus a build-time GitHub download of `silero_vad.onnx` | Neither needed; torch is already present and the wheel bundles `silero_vad.jit` |

---

## Motivation

`dalston/realtime_sdk/vad.py` calls `load_silero_session()` directly, which raises if `onnxruntime` is absent. The batch chunker in `dalston/engine_sdk/vad.py` does **not** have this problem — it tries the `silero-vad` pip package first (torch JIT, bundled offline in the wheel) and only falls back to ONNX. Realtime never received the same treatment.

The result is an inconsistency that bites per-image rather than once:

| Base image | `onnxruntime` | torch | Realtime VAD today |
| ---------- | ------------- | ----- | ------------------ |
| `base-nemo` | yes (added in PR #349) | yes | works |
| `base-pytorch` | **no** | yes | **broken** |
| `base-pyannote` | **no** | yes | **broken** |
| `base-onnx` | yes | no | works |
| `base-engine` | yes | no | works |

`base-pytorch` and `base-pyannote` are latent instances of the same bug — any realtime engine landing on them fails exactly as NeMo did. Patching each image with `onnxruntime-gpu` means three redundant CUDA runtimes and three chances to forget.

Verified 2026-08-04 inside `stt-transcribe-nemo` with **no** `onnxruntime` installed:

```
onnxruntime: NO -> ModuleNotFoundError
torch: 2.13.0+cu130  cuda: True
load_silero_vad() OK -> RecursiveScriptModule
inference OK, speech prob: 0.0016698105027899146
has reset_states: True
```

So the torch path needs zero new dependencies on any torch-based image, and exposes `reset_states()` — the exact streaming primitive `SileroOnnxModel` hand-rolls around the ONNX graph's explicit hidden-state inputs.

Accuracy is not a factor. `silero_vad.jit` (2,272,526 bytes) and `silero_vad.onnx` (2,327,524 bytes) are the same fp32 v5.1.2 weights in two serializations; differences are export-level numerical noise against a 0.5 threshold. The fp16 (`silero_vad_half.onnx`) and 16 kHz-only variants **would** differ and are deliberately not used.

---

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│              dalston/engine_sdk/silero_vad.py                 │
│                                                               │
│   load_silero_model()          ← NEW: picks a backend         │
│        │                                                      │
│        ├── 1. torch JIT   → SileroTorchModel   (NEW)          │
│        │      via silero_vad.load_silero_vad()                │
│        │                                                      │
│        └── 2. onnxruntime → SileroOnnxModel    (existing)     │
│               via load_silero_session()                       │
│                                                               │
│   Both satisfy the same protocol:                             │
│       __call__(audio: np.ndarray, sample_rate: int) -> float  │
│       reset_states(batch_size: int = 1) -> None               │
└──────────────────────────────────────────────────────────────┘
              ▲                                ▲
              │                                │
   realtime_sdk/vad.py                 engine_sdk/vad.py
   (switches to loader)          (already two-tier; unchanged)
```

---

## Steps

### 100.1: Add `SileroTorchModel` and the two-tier loader

**Files modified:**

- `dalston/engine_sdk/silero_vad.py` — add wrapper + loader, keep existing ONNX API intact

**Deliverables:**

A wrapper presenting the identical interface to `SileroOnnxModel`, so callers are backend-agnostic:

```python
class SileroTorchModel:
    """Silero VAD v5 via the TorchScript model bundled in the silero-vad wheel.

    Presents the same interface as :class:`SileroOnnxModel`. The TorchScript
    module owns its recurrent state internally, so this wrapper only adapts
    numpy<->torch and normalises the return type.
    """

    def __init__(self, module: Any) -> None: ...

    def __call__(self, audio: np.ndarray, sample_rate: int) -> float: ...

    def reset_states(self, batch_size: int = 1) -> None: ...
```

And the selector, mirroring the resolution-order docstring style already used by `VadChunker._ensure_model`:

```python
def load_silero_model() -> Any:
    """Load a Silero VAD v5 model on whichever runtime is available.

    Resolution order (first success wins):

    1. ``silero_vad`` pip package torch JIT — preferred on torch-based
       images (base-nemo, base-pytorch, base-pyannote). Offline: the
       wheel bundles ``silero_vad.jit``.
    2. ``onnxruntime`` via :func:`load_silero_session` — for images with
       ONNX but no torch (base-onnx, base-engine).

    Raises:
        RuntimeError: if neither backend is available, naming both
            install options.
    """
```

Notes for the implementer:

- `reset_states(batch_size)` — the TorchScript module's `reset_states()` takes no batch argument. Accept and ignore `batch_size` for interface parity, but log at debug if a caller passes anything other than 1 so a future batched caller is not silently mis-served.
- Convert with `torch.from_numpy(audio)`; the module returns a tensor — use `.item()` (not `float()` on a grad-tracking tensor, which emits `UserWarning: Converting a tensor with requires_grad=True to a scalar`). Observed during investigation.
- Do **not** import `torch` at module scope. `engine_sdk/silero_vad.py` is imported by `base-onnx` images that have no torch. Import inside the loader, same as the existing `onnxruntime` import.
- Emit `logger.info("silero_vad_loaded", backend=...)` with `"silero_vad_pkg"` / `"onnxruntime"` to match the values `engine_sdk/vad.py` already logs.

---

### 100.2: Switch realtime VAD to the loader

**Files modified:**

- `dalston/realtime_sdk/vad.py` — replace the hard `load_silero_session()` call in `_load_model`

**Deliverables:**

Current (`_load_model`, ~line 127):

```python
session = load_silero_session()
self._model = SileroOnnxModel(session)
logger.info("silero_vad_loaded", backend="onnxruntime")
```

Becomes a single `load_silero_model()` call, with the backend logged by the loader.

Preserve the existing `logger.error("silero_vad_load_failed", ...)` + re-raise on total failure — a realtime session with no VAD backend must still fail loudly, not silently degrade.

Keep the `_SileroOnnxModel` alias at the top of the file (it exists to avoid churn for external importers), but retype `self._model` to the shared protocol so it can hold either backend.

---

### 100.3: Drop `onnxruntime` from `base-nemo`

**Files modified:**

- `docker/Dockerfile.base-nemo` — remove the `onnxruntime` install added in PR #349; remove the build-time `silero_vad.onnx` download

**Deliverables:**

Revert the PR #349 `onnxruntime` / `onnxruntime-gpu` block. Torch is already present, so step 100.1 covers this image.

Also remove the `urllib.request.urlretrieve` of `silero_vad.onnx` and the `DALSTON_SILERO_VAD_ONNX` env var **only if** step 100.1's torch path is confirmed to take precedence — the pip wheel already bundles both `.jit` and `.onnx`, making the download redundant. Keep them if the batch chunker's `_try_load_onnx_env` is judged worth retaining as a belt-and-braces path on this image; call the decision out in the PR either way.

Do **not** touch `base-onnx` or `base-engine` — they have no torch and must keep the ONNX path.

---

### 100.4: Tests

**Files modified:**

- `tests/unit/test_silero_vad_loader.py` *(new)*

**Deliverables:**

- `SileroTorchModel` returns a plain `float` in `[0, 1]` and emits no `UserWarning`
- `load_silero_model()` prefers torch when both backends are importable
- `load_silero_model()` falls back to ONNX when `silero_vad` import fails
- `load_silero_model()` raises `RuntimeError` naming both install options when neither is available
- `reset_states(batch_size=1)` is a no-op pass-through; `batch_size != 1` logs
- Both backends satisfy the same protocol (parametrised interface test)

Use monkeypatched import failures rather than requiring both runtimes in CI. The existing test suite runs without torch or onnxruntime installed, so these must not become import-gated.

---

### 100.5: Give the torch images a usable backend

**Files modified:**

- `docker/Dockerfile.base-pytorch` — install the `silero-vad` package
- `docker/Dockerfile.base-pyannote` — install the `silero-vad` package

**Deliverables:**

A backend-agnostic loader does not help an image that has *neither*
backend. Discovered during review of 100.1:

- `base-pytorch` bakes the ONNX **file** and sets
  `DALSTON_SILERO_VAD_ONNX`, but installs neither `silero-vad` nor
  `onnxruntime` — so the baked file is inert and both loader branches
  fail. This image hosts `faster-whisper` and `hf-asr`, both
  realtime-capable, so the gap is live rather than theoretical.
- `base-pyannote` has no VAD provisioning whatsoever.

Both get `silero-vad` (~8 MB, pure Python, TorchScript weights bundled)
rather than `onnxruntime` (~200–400 MB with CUDA), since both images
already have torch.

---

### 100.6: Align Silero package pins with baked ONNX artifacts

**Files modified:**

- `docker/Dockerfile.base-*` — one Silero release across the pin and the baked URL
- `dalston/engine_sdk/silero_vad.py` — `_SILERO_VAD_ONNX_URL`

**Deliverables:**

The two backends are only equivalent when an image's `silero-vad` pin
and its baked ONNX come from the same release. Today they do not:

| Location | Version |
| -------- | ------- |
| Every `Dockerfile.base-*` baked ONNX URL | v5.1.2 |
| `_SILERO_VAD_ONNX_URL` runtime fallback | v5.1.2 |
| `Dockerfile.base-nemo` package pin | `>=5.1` (unbounded) |
| `engines/stt-transcribe/nemo/requirements.txt` | `>=6.2.1` |

Verified the artifacts genuinely differ across releases — the ONNX has
identical byte size but a different sha256 (`2623a295…` v5.1.2 vs
`1a153a22…` v6.2.1), and the `.jit` differs in size and hash. The
installed `.jit` in a running NeMo container is 2,272,526 bytes = v6.2.1,
so that image already runs v6 on the torch path while pointing
`DALSTON_SILERO_VAD_ONNX` at v5.1.2.

Consequence: backend selection currently changes the VAD model, and
therefore speech probabilities and endpointing. Pick one release, move
every pin and baked URL to it together, and note the coupling so they
cannot drift again.

---

## Non-Goals

- **Removing `onnxruntime` from `base-onnx` / `base-engine`** — those images have no torch; ONNX is the correct backend there.
- **Changing VAD tuning, thresholds, or the streaming state machine** — this is a backend-selection change only. `VADConfig` semantics are untouched.
- **Switching the batch chunker's resolution order** — `engine_sdk/vad.py` already prefers the pip package and works; it only gains the shared wrapper indirectly.
- **fp16 / 16 kHz-only Silero variants** — measurably different outputs; out of scope.
- **Benchmarking torch vs ONNX VAD latency** — accuracy is equivalent and both are trivial next to ASR inference. Worth a separate look only if VAD ever shows up in a profile.

---

## Deployment

Ordering matters because the code change must ship **before** the image change:

1. Merge 100.1 + 100.2 + 100.4 (SDK + tests). Harmless on every image — `base-nemo` still has `onnxruntime` from PR #349 as a fallback.
2. Let `build-engine-nemo.yml` rebuild (`docker/Dockerfile.base-nemo` and `dalston/**` are both in its trigger paths; the workflow builds the base before the engine).
3. Only then merge 100.3, which removes `onnxruntime` from the image.

Reversing that order leaves a window where an image has neither backend.

A **new** GPU worker is required to pick up either change — a running worker keeps its image for its lifetime. With `min_instances: 0` and `scale_down_after_s: 2100`, the practical sequence is: merge → wait for GHCR build → cycle the worker → verify.

---

## Verification

```bash
# 1. Torch path selected on a torch image with NO onnxruntime present.
#    Run inside a running NeMo engine container.
docker exec stt-transcribe-nemo python3 -c "
from dalston.engine_sdk.silero_vad import load_silero_model
import numpy as np
m = load_silero_model()
print('backend class:', type(m).__name__)          # expect SileroTorchModel
p = m(np.zeros(512, dtype=np.float32), 16000)
print('prob:', p, type(p).__name__)                # expect float, near 0.0
m.reset_states()
print('reset OK')
"

# 2. ONNX path still selected where torch is absent (base-onnx engine).
docker exec stt-transcribe-onnx python3 -c "
from dalston.engine_sdk.silero_vad import load_silero_model
print('backend class:', type(load_silero_model()).__name__)  # expect SileroOnnxModel
"

# 3. End-to-end: a realtime session survives past the 1s VAD-load failure point.
#    Watch for realtime_session_finalized arriving well after creation, and no
#    'onnxruntime is required' error.
docker logs dalston-gateway-1 --since 5m 2>&1 \
  | grep -E "session_allocated|realtime_session_created|realtime_session_finalized|onnxruntime"

# 4. Confirm the engine registered a Tailscale-routable realtime endpoint
#    (unrelated to VAD, but the same worker cycle proves both).
docker exec dalston-redis-1 redis-cli --scan --pattern "dalston:engine:instance:nemo-rt-*"
```

Browser check: `https://<control-plane>.<tailnet>.ts.net/console/realtime/live` — start a session and confirm transcripts stream while speaking, rather than the session ending immediately.

---

## Checkpoint

- [ ] `SileroTorchModel` implements `__call__` and `reset_states` with the same signatures as `SileroOnnxModel`
- [ ] `load_silero_model()` prefers torch, falls back to ONNX, raises naming both when neither is present
- [ ] `torch` is not imported at module scope in `engine_sdk/silero_vad.py`
- [ ] `realtime_sdk/vad.py` uses the loader and still fails loudly when no backend exists
- [ ] Unit tests pass without torch or onnxruntime installed (monkeypatched imports)
- [ ] Realtime session on a NeMo worker streams transcripts instead of dying at ~1s
- [ ] Realtime VAD works on a `base-pytorch` or `base-pyannote` engine with no image change
- [ ] `base-onnx` / `base-engine` still select `SileroOnnxModel`
- [ ] `onnxruntime` removed from `base-nemo` only after the SDK change has shipped
