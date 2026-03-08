# M61: OpenAI API Parity

| | |
|---|---|
| **Goal** | Close all actionable gaps between Dalston's OpenAI-compatible API and the current OpenAI ASR spec |
| **Duration** | Phase 1: 2 weeks · Phase 2: 5 weeks · Phase 3: 10 weeks |
| **Dependencies** | M38 (OpenAI compat base), M48 (realtime routing), M56 (lite pipeline) |
| **Deliverable** | Dalston passes the OpenAI Python SDK test suite and behaves correctly under spec-compliant clients |
| **Status** | Not started |
| **Gap Reference** | [`docs/specs/openai/PARITY_GAPS.md`](../../specs/openai/PARITY_GAPS.md) |

## User Story

> *"As a developer migrating from OpenAI, I can point the OpenAI Python SDK at Dalston and get identical results — the same fields, the same event sequence, the same error format — without reading Dalston's documentation."*

> *"As a realtime telephony client sending 24 kHz PCM, I don't receive garbled, slow-motion transcription output."*

---

## Problem

The original M38 implementation established the structural scaffold for OpenAI compatibility.
What remains is a set of fidelity gaps that cause silent failures and observable behavioural
divergence under spec-compliant clients:

```
BATCH ENDPOINT GAPS
───────────────────────────────────────────────────────────────
  SILENT BUGS (client receives wrong data or wrong format)
  ├── diarized_json response_format falls back to json silently
  ├── temperature=0 never forwarded to engine
  ├── prompt raw-string passed where engine expects term array
  ├── usage{} field absent from all response objects
  └── model field absent from response objects

  MISSING PARAMETERS
  ├── stream=true SSE streaming  [hard — Phase 3]
  ├── include=["logprobs"]       [medium — Phase 2]
  ├── chunking_strategy          [medium — Phase 2]
  └── known_speaker_names        [medium — Phase 2]
      known_speaker_references   [hard — Phase 3]

REALTIME ENDPOINT GAPS
───────────────────────────────────────────────────────────────
  CORRECTNESS BUG
  └── pcm16 treated as 16 kHz, OpenAI clients send 24 kHz
      → audio plays at 0.67× speed, transcription is garbled

  MISSING EVENTS
  ├── conversation.item.created not emitted after buffer commit
  ├── input_audio_buffer.committed missing previous_item_id
  └── speech_started / speech_stopped missing item_id

  SILENTLY IGNORED CONFIG
  └── turn_detection.{threshold, silence_duration_ms,
      prefix_padding_ms} discarded

BLIND SPOTS
───────────────────────────────────────────────────────────────
  ├── Rate-limit headers (x-ratelimit-*) never returned
  ├── prompt token length not enforced
  ├── URL-based audio downloads bypass 25 MB limit
  └── sk- prefixed OpenAI keys accepted silently with no warning
```

---

## Phase 1: Gateway Fidelity (Weeks 1–2)

All changes in this phase are confined to the gateway layer. No engine, worker, or pipeline
schema changes. Each step is independently deployable.

---

### 1.1: Fix `diarized_json` silent fallback

**Gap**: G-6 — `diarized_json` is listed in the form description and in the OpenAI spec
but is absent from `OPENAI_RESPONSE_FORMATS`. Clients requesting it receive plain `json`
with no error or indication.

**File**: `dalston/gateway/api/v1/openai_audio.py`

Add `DIARIZED_JSON` to the enum:

```python
class OpenAIResponseFormat(StrEnum):
    JSON = "json"
    TEXT = "text"
    SRT = "srt"
    VERBOSE_JSON = "verbose_json"
    VTT = "vtt"
    DIARIZED_JSON = "diarized_json"   # ADD
```

Add response model:

```python
class OpenAIUtterance(BaseModel):
    """One speaker's contiguous utterance in diarized_json response."""
    speaker: str                    # "speaker_0", "speaker_1", or provided name
    start: float
    end: float
    text: str


class OpenAIDiarizedResponse(BaseModel):
    """OpenAI diarized_json response (speaker-attributed)."""
    utterances: list[OpenAIUtterance]
    usage: dict
```

Add branch in `format_openai_response()` before the default JSON branch:

```python
if response_format == OpenAIResponseFormat.DIARIZED_JSON.value:
    utterances = []
    for seg in transcript.get("segments", []):
        utterances.append(
            OpenAIUtterance(
                speaker=seg.get("speaker_id", "speaker_0"),
                start=seg.get("start", 0.0),
                end=seg.get("end", 0.0),
                text=seg.get("text", ""),
            )
        )
    return OpenAIDiarizedResponse(
        utterances=utterances,
        usage={"type": "audio", "audio_seconds": duration},
    ).model_dump()
```

---

### 1.2: Add `usage` field to all response types

**Gap**: G-7 — The `usage` object is absent from `json`, `verbose_json`, and `diarized_json`
responses. OpenAI clients and billing tooling depend on it.

**File**: `dalston/gateway/api/v1/openai_audio.py`

Add `usage` to existing response models:

```python
class OpenAITranscriptionResponse(BaseModel):
    text: str
    model: str = ""              # see 1.3
    usage: dict = Field(default_factory=dict)


class OpenAIVerboseResponse(BaseModel):
    task: str = "transcribe"
    language: str
    duration: float
    text: str
    segments: list[OpenAISegment] = Field(default_factory=list)
    words: list[OpenAIWord] | None = None
    model: str = ""              # see 1.3
    usage: dict = Field(default_factory=dict)
```

Populate in `format_openai_response()`. Add a helper at the top:

```python
def _build_usage(duration: float) -> dict:
    return {"type": "audio", "audio_seconds": round(duration, 3)}
```

Pass `usage=_build_usage(duration)` to both response constructors.

---

### 1.3: Echo `model` in batch responses

**Gap**: B-3 — OpenAI responses include the model that processed the request. Dalston
returns no `model` field, making it impossible for clients to audit or log which engine
handled a job.

**File**: `dalston/gateway/api/v1/openai_audio.py`

Update `format_openai_response()` signature:

```python
def format_openai_response(
    transcript: dict[str, Any],
    response_format: str,
    timestamp_granularities: list[str] | None,
    export_service: ExportService,
    model: str = "",              # ADD
) -> Response | dict[str, Any]:
```

Populate from the resolved engine identifier. In `transcription.py`, after the job
completes and before calling `format_openai_response`, extract the engine from the job:

```python
effective_model = job.model or settings.default_model
return format_openai_response(
    transcript, response_format, timestamp_granularities,
    export_service, model=effective_model,
)
```

---

### 1.4: Fix `temperature=0` not forwarded

**Gap**: G-8 — Both transcription and translation skip forwarding `temperature` when
its value is `0`, which is the most common explicit setting (disable sampling).

**File**: `dalston/gateway/api/v1/transcription.py` (line ~343) and
`dalston/gateway/api/v1/openai_translation.py` (line ~156)

Change both occurrences from:

```python
if temperature is not None and temperature > 0:
    parameters["temperature"] = temperature
```

to:

```python
if temperature is not None:
    parameters["temperature"] = temperature
```

---

### 1.5: Fix `prompt` → vocabulary mapping

**Gap**: B-1 — The batch path passes `prompt` as a raw string to `vocabulary`, which the
engine expects to be a term list or JSON array. The realtime path splits on commas.
Neither correctly models OpenAI's intent (a priming prose string, max 224 tokens).

The pragmatic mapping: split on whitespace boundaries and deduplicate, capped at 100 terms.
This approximates vocabulary boosting for the most common use case (comma-separated term
lists) while not breaking prose hints.

**File**: `dalston/gateway/api/v1/openai_audio.py`

Add helper:

```python
def prompt_to_vocabulary(prompt: str | None) -> list[str] | None:
    """Convert OpenAI prompt string to Dalston vocabulary term list.

    OpenAI's prompt is free prose used for priming context. Dalston's
    vocabulary is a list of domain terms to boost recognition of.
    We split on commas and whitespace as a best-effort approximation.
    """
    if not prompt:
        return None
    # Prefer comma-separated if commas present (common pattern)
    if "," in prompt:
        terms = [t.strip() for t in prompt.split(",") if t.strip()]
    else:
        terms = prompt.split()
    # Deduplicate, preserve order, cap at 100
    seen: set[str] = set()
    unique = []
    for t in terms:
        if t not in seen:
            seen.add(t)
            unique.append(t)
    return unique[:100] or None
```

Apply in `transcription.py` OpenAI mode parameter building:

```python
if prompt:
    vocab = prompt_to_vocabulary(prompt)
    if vocab:
        parameters["vocabulary"] = vocab
```

Apply in `openai_translation.py`:

```python
if prompt:
    vocab = prompt_to_vocabulary(prompt)
    if vocab:
        parameters["vocabulary"] = vocab
```

Apply in `openai_realtime.py` `_handle_session_update()`:

```python
if "prompt" in transcription:
    session_config["vocabulary"] = prompt_to_vocabulary(transcription["prompt"])
```

---

### 1.6: Enforce `prompt` token length

**Gap**: B-4 — The 224-token limit on `prompt` is documented but never validated.

**File**: `dalston/gateway/api/v1/openai_audio.py`

Add to `validate_openai_request()`:

```python
# Validate prompt length (OpenAI limit: 224 tokens ≈ 900 characters heuristic)
_PROMPT_MAX_CHARS = 900
if prompt and len(prompt) > _PROMPT_MAX_CHARS:
    raise_openai_error(
        400,
        f"prompt is too long ({len(prompt)} characters). "
        f"Maximum is approximately 224 tokens (~{_PROMPT_MAX_CHARS} characters).",
        param="prompt",
        code="prompt_too_long",
    )
```

Update the function signature to accept `prompt`:

```python
def validate_openai_request(
    model: str,
    response_format: str | None,
    timestamp_granularities: list[str] | None,
    prompt: str | None = None,     # ADD
) -> None:
```

Call site in `transcription.py`:

```python
validate_openai_request(model, response_format, timestamp_granularities, prompt=prompt)
```

---

### 1.7: Add rate-limit response headers

**Gap**: B-2 — OpenAI clients use `x-ratelimit-*` headers for adaptive backoff. Dalston
returns a plain 429 with no timing information, causing clients to fall back to
exponential backoff.

**File**: `dalston/gateway/services/rate_limiter.py`

Extend `RateLimitResult` to carry header values:

```python
@dataclass
class RateLimitResult:
    allowed: bool
    limit: int
    remaining: int
    reset_after_seconds: float      # ADD
```

**File**: `dalston/gateway/middleware/rate_limit.py` (or wherever the 429 response is built)

Inject headers on 429 responses:

```python
from fastapi.responses import JSONResponse

return JSONResponse(
    status_code=429,
    content={"error": {"message": "Rate limit exceeded", "type": "rate_limit_error",
                        "code": "rate_limit_exceeded"}},
    headers={
        "x-ratelimit-limit-requests": str(result.limit),
        "x-ratelimit-remaining-requests": str(result.remaining),
        "x-ratelimit-reset-requests": f"{result.reset_after_seconds:.3f}s",
        "retry-after": str(int(result.reset_after_seconds) + 1),
    },
)
```

Also inject on successful responses (remaining capacity):

```python
response.headers["x-ratelimit-limit-requests"] = str(result.limit)
response.headers["x-ratelimit-remaining-requests"] = str(result.remaining)
```

---

### 1.8: Warn on `sk-` prefixed OpenAI keys

**Gap**: B-8 — Clients accidentally pointing their real OpenAI SDK at a Dalston instance
will have their `sk-xxx` key silently rejected with a generic 401. A descriptive error
helps diagnosis.

**File**: `dalston/gateway/middleware/auth.py`

In the key validation path, before the standard 401:

```python
if raw_key.startswith("sk-"):
    raise HTTPException(
        status_code=401,
        detail={
            "error": {
                "message": (
                    "The API key provided appears to be an OpenAI key (sk- prefix). "
                    "Use a Dalston API key (dk_ prefix) for this endpoint."
                ),
                "type": "invalid_api_key",
                "code": "openai_key_detected",
            }
        },
    )
```

---

### 1.9: Realtime — emit `conversation.item.created`

**Gap**: G-12 — After `input_audio_buffer.commit`, OpenAI emits `conversation.item.created`
before `input_audio_buffer.committed`. Clients that maintain a conversation item graph
(e.g. the official `openai` Node/Python SDKs) expect this event.

**File**: `dalston/gateway/api/v1/openai_realtime.py`

In `_openai_client_to_worker()`, `input_audio_buffer.commit` branch, before the `committed` ack:

```python
elif msg_type == "input_audio_buffer.commit":
    await worker_ws.send(json.dumps({"type": "flush"}))
    prev_item_id = session_state.current_item_id          # save before rotating
    session_state.current_item_id = generate_item_id()

    # conversation.item.created (OpenAI spec requirement)
    await client_ws.send_json({
        "type": "conversation.item.created",
        "event_id": generate_event_id(),
        "previous_item_id": prev_item_id,
        "item": {
            "id": session_state.current_item_id,
            "type": "message",
            "role": "user",
            "content": [{"type": "input_audio", "audio": None, "transcript": None}],
        },
    })

    # input_audio_buffer.committed
    await client_ws.send_json({
        "type": "input_audio_buffer.committed",
        "event_id": generate_event_id(),
        "previous_item_id": prev_item_id,
        "item_id": session_state.current_item_id,
    })
```

---

### 1.10: Realtime — add `previous_item_id` and `item_id` to session events

**Gaps**: G-13, G-14

**File**: `dalston/gateway/api/v1/openai_realtime.py`

Extend `OpenAISessionState`:

```python
@dataclass
class OpenAISessionState:
    current_item_id: str = field(default_factory=generate_item_id)
    previous_item_id: str | None = None          # ADD

    def rotate_item(self) -> str:
        """Rotate to a new item, returning the old id."""
        old = self.current_item_id
        self.previous_item_id = old
        self.current_item_id = generate_item_id()
        return old
```

In `_openai_worker_to_client()`, add `item_id` to VAD events:

```python
elif msg_type == "vad.speech_start":
    translated = {
        "type": "input_audio_buffer.speech_started",
        "event_id": generate_event_id(),
        "item_id": session_state.current_item_id,    # ADD
        "audio_start_ms": int(data.get("timestamp", 0) * 1000),
    }

elif msg_type == "vad.speech_end":
    translated = {
        "type": "input_audio_buffer.speech_stopped",
        "event_id": generate_event_id(),
        "item_id": session_state.current_item_id,    # ADD
        "audio_end_ms": int(data.get("timestamp", 0) * 1000),
    }
```

Update `input_audio_buffer.commit` to use `session_state.rotate_item()` (see 1.9).

---

### 1.11: Realtime — enrich `transcription_session.created`

**Gap**: G-16 — The session created event lacks `turn_detection` defaults and
`noise_reduction`, which some SDKs inspect immediately after connection.

**File**: `dalston/gateway/api/v1/openai_realtime.py`

Replace the sparse session object in `openai_realtime_transcription()`:

```python
await websocket.send_json({
    "type": "transcription_session.created",
    "event_id": generate_event_id(),
    "session": {
        "id": openai_session_id,
        "object": "realtime.transcription_session",
        "model": model,
        "input_audio_format": "pcm16",
        "input_audio_transcription": {
            "model": model,
            "language": None,
            "prompt": None,
        },
        "turn_detection": {
            "type": "server_vad",
            "threshold": 0.5,
            "silence_duration_ms": 500,
            "prefix_padding_ms": 300,
        },
        "noise_reduction": None,
        "input_audio_noise_reduction": None,
    },
})
```

---

### Phase 1 Verification

```bash
# G-6: diarized_json now returns speaker-attributed utterances
curl -s -X POST http://localhost:8000/v1/audio/transcriptions \
  -H "Authorization: Bearer dk_test" \
  -F "file=@tests/fixtures/audio/stereo_two_speakers.wav" \
  -F "model=whisper-1" \
  -F "response_format=diarized_json" | jq '.utterances[0].speaker'
# Expected: "speaker_0"

# G-7: usage field present
curl -s ... -F "response_format=json" | jq '.usage.audio_seconds'
# Expected: numeric value

# G-8: temperature=0 visible in engine logs
# Check logs for "temperature": 0 in transcription engine

# G-16: session.created has turn_detection
# Connect to /v1/realtime, first event should have session.turn_detection.threshold

# B-4: long prompt rejected
curl -s -X POST http://localhost:8000/v1/audio/transcriptions \
  -H "Authorization: Bearer dk_test" \
  -F "file=@audio.mp3" \
  -F "model=whisper-1" \
  -F "prompt=$(python3 -c 'print("word " * 200)')" | jq '.error.code'
# Expected: "prompt_too_long"

# B-8: OpenAI key gives helpful error
curl -s -X POST http://localhost:8000/v1/audio/transcriptions \
  -H "Authorization: Bearer sk-proj-abc123" | jq '.error.code'
# Expected: "openai_key_detected"

# Realtime: conversation.item.created emitted
# Use the openai Python SDK realtime client and trace events
```

### Phase 1 Checkpoint

- [ ] `diarized_json` returns speaker-attributed `utterances` array
- [ ] `usage.audio_seconds` present in `json`, `verbose_json`, `diarized_json` responses
- [ ] `model` field echoed in `json` and `verbose_json` responses
- [ ] `temperature=0` forwarded to engine in both transcription and translation
- [ ] `prompt` canonically split into vocabulary terms in batch and realtime paths
- [ ] `prompt` rejected with 400 when > 900 characters
- [ ] `x-ratelimit-*` headers returned on 429 and successful responses
- [ ] `sk-` prefixed keys return descriptive 401 with `openai_key_detected` code
- [ ] `conversation.item.created` emitted on buffer commit
- [ ] `input_audio_buffer.committed` includes `previous_item_id`
- [ ] `speech_started` and `speech_stopped` include `item_id`
- [ ] `transcription_session.created` includes `turn_detection` defaults and `noise_reduction: null`

---

## Phase 2: Protocol and Pipeline Fidelity (Weeks 3–7)

Changes in this phase cross service boundaries: realtime worker protocol, batch engine
output schema, DAG builder, and merge engine. Each step should be validated independently
before moving to the next.

---

### 2.1: Realtime PCM16 sample rate — resampling to 24 kHz contract

**Gap**: G-11 — This is the most impactful correctness bug. OpenAI's `pcm16` is 24 kHz.
Dalston accepts it at 16 kHz, producing speed-shifted audio and incorrect transcription.

**Strategy**: Advertise our native 16 kHz in the session config to spec-compliant clients
via `session.input_audio_format` defaulting to `pcm16_16k` (a Dalston-defined extension),
while also accepting the spec's `pcm16` (24 kHz) and resampling it to 16 kHz in the
realtime worker. This preserves backward compatibility for existing Dalston clients while
being correct for OpenAI clients.

**Step A — Realtime worker resampling** (`engines/realtime/*/engine.py`)

In the audio ingestion path of each realtime worker, before passing to VAD:

```python
import numpy as np

_SUPPORTED_SAMPLE_RATES = {8000, 16000}

def resample_if_needed(audio_bytes: bytes, source_rate: int, target_rate: int) -> bytes:
    """Resample PCM16 audio from source_rate to target_rate."""
    if source_rate == target_rate:
        return audio_bytes
    samples = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32)
    ratio = target_rate / source_rate
    new_length = int(len(samples) * ratio)
    resampled = np.interp(
        np.linspace(0, len(samples), new_length),
        np.arange(len(samples)),
        samples,
    ).astype(np.int16)
    return resampled.tobytes()
```

**Step B — Worker URL protocol extension**

Extend `_build_worker_params()` in `openai_realtime.py` to pass the client-declared
sample rate:

```python
params["client_sample_rate"] = str(config["sample_rate"])
params["sample_rate"] = str(DEFAULT_SAMPLE_RATE)   # worker's native rate
```

The worker reads `client_sample_rate`, runs the resample if different from `sample_rate`,
then processes at `sample_rate`.

**Step C — Update `OPENAI_AUDIO_FORMAT_MAP`**

```python
OPENAI_AUDIO_FORMAT_MAP = {
    # client_encoding, client_rate, worker_rate
    "pcm16":    ("pcm_s16le", 24000, DEFAULT_SAMPLE_RATE),  # resample 24→16 kHz
    "g711_ulaw": ("mulaw",     8000,  8000),
    "g711_alaw": ("alaw",      8000,  8000),
}
```

---

### 2.2: Realtime `turn_detection` parameters forwarded to worker

**Gap**: G-15 — VAD threshold, silence duration, and prefix padding from OpenAI's
`turn_detection` object are silently discarded. Silero VAD supports all three natively.

**Step A** — Extract values in `_handle_session_update()`:

```python
turn_detection = session.get("turn_detection")
if turn_detection is None:
    session_config["enable_vad"] = False
elif isinstance(turn_detection, dict):
    session_config["enable_vad"] = True
    session_config["vad_threshold"] = turn_detection.get("threshold", 0.5)
    session_config["vad_silence_ms"] = turn_detection.get("silence_duration_ms", 500)
    session_config["vad_prefix_ms"] = turn_detection.get("prefix_padding_ms", 300)
```

**Step B** — Forward via `_build_worker_params()`:

```python
if config.get("enable_vad"):
    params["vad_threshold"] = str(config.get("vad_threshold", 0.5))
    params["vad_silence_ms"] = str(config.get("vad_silence_ms", 500))
    params["vad_prefix_ms"] = str(config.get("vad_prefix_ms", 300))
```

**Step C** — Realtime worker session handler reads and applies them to Silero VAD init.
Each worker's `engine.py` already accepts query parameters for session config; add:

```python
vad_threshold: float = float(params.get("vad_threshold", 0.5))
vad_silence_ms: int = int(params.get("vad_silence_ms", 500))
vad = SileroVAD(threshold=vad_threshold, min_silence_duration_ms=vad_silence_ms)
```

---

### 2.3: Accept and validate `chunking_strategy`

**Gap**: G-3 — OpenAI accepts `chunking_strategy: "auto"`. Not accepting it causes
validation errors in client-side code that always includes it.

**File**: `dalston/gateway/api/v1/transcription.py`

Add parameter to the form handler:

```python
chunking_strategy: Annotated[
    str | None,
    Form(description='OpenAI: Chunking strategy ("auto" only)'),
] = None,
```

Add validation in `validate_openai_request()`:

```python
_VALID_CHUNKING_STRATEGIES = {"auto"}
if chunking_strategy and chunking_strategy not in _VALID_CHUNKING_STRATEGIES:
    raise_openai_error(
        400,
        f"Invalid chunking_strategy: {chunking_strategy!r}. Supported: auto.",
        param="chunking_strategy",
        code="invalid_chunking_strategy",
    )
```

`"auto"` is Dalston's existing default behaviour (VAD-based segmentation), so no
downstream plumbing is needed at this stage.

---

### 2.4: `known_speaker_names` → merge engine relabelling

**Gap**: G-4 — OpenAI allows callers to provide speaker name hints that substitute for
generic `SPEAKER_0`/`SPEAKER_1` labels. This is fully achievable with current architecture
by adding a relabelling pass in the merge engine.

**Step A** — Accept parameter in gateway (`transcription.py`):

```python
known_speaker_names: Annotated[
    str | None,
    Form(description="OpenAI: JSON array of speaker names e.g. '[\"Alice\",\"Bob\"]'"),
] = None,
```

Parse and forward in OpenAI mode parameter building:

```python
if known_speaker_names:
    try:
        names = json.loads(known_speaker_names)
        if isinstance(names, list) and all(isinstance(n, str) for n in names):
            parameters["known_speaker_names"] = names
    except (json.JSONDecodeError, TypeError):
        raise_openai_error(
            400,
            "known_speaker_names must be a JSON array of strings",
            param="known_speaker_names",
            code="invalid_parameter",
        )
```

**Step B** — DAG builder passes `known_speaker_names` to merge task parameters.

**Step C** — Final merger engine applies relabelling after assembling segments:

```python
def _relabel_speakers(
    segments: list[dict], known_names: list[str]
) -> list[dict]:
    """Map SPEAKER_0, SPEAKER_1, ... to provided names in order of first appearance."""
    label_to_name: dict[str, str] = {}
    assignment_index = 0
    for seg in segments:
        label = seg.get("speaker_id", "")
        if label and label not in label_to_name and assignment_index < len(known_names):
            label_to_name[label] = known_names[assignment_index]
            assignment_index += 1
    for seg in segments:
        if seg.get("speaker_id") in label_to_name:
            seg = {**seg, "speaker_id": label_to_name[seg["speaker_id"]]}
    return segments
```

---

### 2.5: Real quality signals in verbose JSON

**Gaps**: G-2, G-18 — `avg_logprob`, `no_speech_prob`, `compression_ratio`, and `tokens`
are hardcoded stubs. `faster-whisper` emits real values per segment; they are currently
discarded before the engine writes its output.

**Step A** — Extend `TranscriptionSegment` in `dalston/common/pipeline_types.py`:

```python
class TranscriptionSegment(BaseModel):
    # existing fields ...
    avg_logprob: float | None = None
    no_speech_prob: float | None = None
    compression_ratio: float | None = None
    tokens: list[int] = Field(default_factory=list)
```

**Step B** — Capture in the faster-whisper engine (`engines/stt-batch-transcribe-faster-whisper-*/engine.py`):

```python
for seg in whisper_segments:
    segments.append({
        # existing fields ...
        "avg_logprob": seg.avg_logprob,
        "no_speech_prob": seg.no_speech_prob,
        "compression_ratio": seg.compression_ratio,
        "tokens": list(seg.tokens) if seg.tokens else [],
    })
```

**Step C** — Final merger preserves these fields when building `transcript.json`.

**Step D** — `format_openai_response()` reads real values instead of hardcoded sentinels:

```python
OpenAISegment(
    id=i,
    seek=0,
    start=seg.get("start", 0.0),
    end=seg.get("end", 0.0),
    text=seg.get("text", ""),
    tokens=seg.get("tokens", []),
    temperature=0.0,
    avg_logprob=seg.get("avg_logprob", -0.5),
    compression_ratio=seg.get("compression_ratio", 1.0),
    no_speech_prob=seg.get("no_speech_prob", 0.02),
)
```

---

### 2.6: URL-based audio download respects 25 MB limit

**Gap**: B-5 — When `audio_url` is used with an OpenAI model, the gateway downloads the
full file before checking its size. A 500 MB URL will exhaust memory before the check runs.

**File**: `dalston/gateway/services/ingestion.py`

In the URL download path, check `Content-Length` before downloading:

```python
async def _download_url(self, url: str, max_bytes: int | None = None) -> bytes:
    async with self._http.stream("GET", url) as resp:
        resp.raise_for_status()
        if max_bytes:
            content_length = int(resp.headers.get("content-length", 0))
            if content_length > max_bytes:
                raise FileTooLargeError(
                    f"Remote file is {content_length / 1024**2:.1f} MB "
                    f"(limit {max_bytes / 1024**2:.0f} MB)"
                )
        chunks = []
        total = 0
        async for chunk in resp.aiter_bytes(chunk_size=65536):
            total += len(chunk)
            if max_bytes and total > max_bytes:
                raise FileTooLargeError(
                    f"Download exceeded {max_bytes / 1024**2:.0f} MB limit"
                )
            chunks.append(chunk)
        return b"".join(chunks)
```

Call with `max_bytes=OPENAI_MAX_FILE_SIZE` when in OpenAI mode.

---

### Phase 2 Verification

```bash
# G-11: 24 kHz audio transcribed correctly
python3 - <<'EOF'
from openai import OpenAI
import soundfile as sf, numpy as np, io

# Generate 24 kHz sine-wave tone with spoken content (use real audio in practice)
client = OpenAI(api_key="dk_test", base_url="http://localhost:8000/v1")
with open("tests/fixtures/audio/24khz_sample.wav", "rb") as f:
    r = client.audio.transcriptions.create(model="whisper-1", file=f)
print(r.text)   # Should not be garbled/slow
EOF

# G-15: VAD threshold applied
# Connect to /v1/realtime, send turn_detection.threshold=0.1
# Observe that VAD fires on softer speech than default

# G-4: known_speaker_names
curl -s -X POST http://localhost:8000/v1/audio/transcriptions \
  -H "Authorization: Bearer dk_test" \
  -F "file=@tests/fixtures/audio/stereo_two_speakers.wav" \
  -F "model=whisper-1" \
  -F "response_format=diarized_json" \
  -F 'known_speaker_names=["Alice","Bob"]' | jq '.utterances[0].speaker'
# Expected: "Alice" (not "speaker_0")

# G-18: real quality signals
curl -s -X POST http://localhost:8000/v1/audio/transcriptions \
  -H "Authorization: Bearer dk_test" \
  -F "file=@audio.mp3" -F "model=whisper-1" \
  -F "response_format=verbose_json" | jq '.segments[0].avg_logprob'
# Expected: a real negative float, not -0.5

# B-5: large URL rejected before download completes
# Point audio_url at a server streaming a 100 MB file, verify 400 arrives quickly
```

### Phase 2 Checkpoint

- [ ] 24 kHz `pcm16` audio correctly resampled to 16 kHz in realtime workers
- [ ] Worker URL protocol accepts `client_sample_rate` and `vad_threshold`, `vad_silence_ms`, `vad_prefix_ms`
- [ ] `turn_detection` threshold and silence params applied to Silero VAD init
- [ ] `chunking_strategy=auto` accepted without error; other values rejected with 400
- [ ] `known_speaker_names` forwarded through DAG to merge engine; speaker labels substituted
- [ ] `avg_logprob`, `no_speech_prob`, `compression_ratio`, `tokens` populated from real engine output
- [ ] URL-based audio download aborted before full download if `Content-Length` exceeds limit
- [ ] Streaming download stops at 25 MB byte count even without `Content-Length` header

---

## Phase 3: New Subsystems (Weeks 8–17)

These items require new infrastructure or significant architectural additions.
Each carries its own design spike before implementation begins.

---

### 3.1: SSE streaming for batch transcription

**Gaps**: G-1, G-9 — `stream=true` on `POST /v1/audio/transcriptions` and
`POST /v1/audio/translations`.

**Architecture**:

```
Client                Gateway               Orchestrator              Engine
  │                     │                        │                      │
  │  POST stream=true   │                        │                      │
  ├────────────────────►│                        │                      │
  │                     │  publish job.created   │                      │
  │                     ├───────────────────────►│                      │
  │                     │                        │  dispatch to engine  │
  │                     │                        ├─────────────────────►│
  │  text/event-stream  │                        │                      │
  │◄────────────────────┤                        │                      │
  │                     │                        │  seg.partial (Redis) │
  │                     │◄──────────────────────────────────────────────┤
  │  transcript.text.delta                       │                      │
  │◄────────────────────┤                        │                      │
  │  transcript.text.done                        │                      │
  │◄────────────────────┤                        │                      │
```

**Step A — Engine SDK: partial segment pub/sub**

Add `publish_partial_segment()` to `dalston/engine_sdk/types.py`:

```python
async def publish_partial_segment(
    self, redis: Redis, text: str, start: float, end: float
) -> None:
    """Publish a partial segment result for SSE streaming consumers."""
    await redis.publish(
        f"dalston:jobs:{self.job_id}:segments",
        json.dumps({"type": "partial", "text": text, "start": start, "end": end}),
    )
```

**Step B — Faster-whisper engine emits partial segments**

In the transcription loop, call `publish_partial_segment` for each Whisper segment
as it is decoded (before accumulating the full result).

**Step C — Gateway SSE handler**

Add SSE response path to `create_transcription()`:

```python
if openai_mode and stream:
    return StreamingResponse(
        _stream_transcription(job_id, redis, response_format),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
```

```python
async def _stream_transcription(
    job_id: UUID, redis: Redis, response_format: str
) -> AsyncGenerator[str, None]:
    pubsub = redis.pubsub()
    await pubsub.subscribe(f"dalston:jobs:{job_id}:segments")
    try:
        async for message in pubsub.listen():
            if message["type"] != "message":
                continue
            data = json.loads(message["data"])
            if data["type"] == "partial":
                yield f"event: transcript.text.delta\ndata: {json.dumps({'delta': data['text']})}\n\n"
            elif data["type"] == "done":
                yield f"event: transcript.text.done\ndata: {json.dumps({'text': data['text']})}\n\n"
                break
    finally:
        await pubsub.unsubscribe(f"dalston:jobs:{job_id}:segments")
```

**Step D — Translation endpoint** gains `stream=true` following the same pattern.

**Design Spike Required**: Define the full SSE event schema to match OpenAI's
`CreateTranscriptionStreamEvent` type (which also includes `logprobs` when requested).
Schedule a 2-day spike before implementation.

---

### 3.2: `include=["logprobs"]` in streaming and verbose responses

**Gap**: G-2 (streaming variant)

Logprobs on the static `verbose_json` path are addressed in Phase 2 (step 2.5).
The streaming path needs per-token logprobs emitted in `transcript.text.delta` events.

**Dependency**: Phase 3.1 must be complete (SSE infrastructure).

**Engine change**: In the faster-whisper engine, when `include_logprobs=true` is set
as a job parameter, capture `token_logprobs` per segment and include them in partial
segment pub/sub messages.

**Gateway**: When `include=["logprobs"]` is in the request, set `include_logprobs=true`
in job parameters and include `logprobs` in each `transcript.text.delta` SSE event:

```json
{
  "type": "transcript.text.delta",
  "delta": "Hello",
  "logprobs": { "token": "Hello", "logprob": -0.08, "bytes": [72, 101, 108, 108, 111] }
}
```

---

### 3.3: `known_speaker_references` — voice-print speaker identification

**Gap**: G-5 — Audio reference clips for matching known speakers by voice print.

**Architecture**:

```
Request
  ├── known_speaker_references: [ { name: "Alice", audio: "data:audio/wav;base64,..." } ]
  └── file: meeting.mp3

Pipeline
  PREPARE → TRANSCRIBE → ALIGN → SPEAKER_EMBED (new) → DIARIZE (ref-aware) → MERGE
                                        ↑
                              Extract embeddings from
                              reference clips, pass as
                              speaker enrollment to diarize
```

**Step A — New pipeline stage: `SPEAKER_EMBED`**

New engine: `engines/speaker-embed/pyannote-embed/`

- Accepts audio clips (the reference files) + the main audio
- Runs pyannote's embedding extractor on each reference clip
- Stores embeddings as `speaker_embeddings.json` in S3 at `tasks/{task_id}/`

**Step B — Diarize engine variant with enrollment**

Extend `engines/stt-diarize-pyannote-*/engine.py` to read `speaker_embeddings.json` if
present and pass embeddings to pyannote's `SpeakerDiarization` pipeline as enrollment
references.

**Step C — Gateway parsing**

```python
known_speaker_references: Annotated[
    str | None,
    Form(description="JSON array of {name, audio} objects (audio as data URI)"),
] = None,
```

Validate, decode the data URIs, upload reference clips to S3, and attach their URIs
to the job parameters for the DAG builder.

**Design Spike Required**: Benchmark pyannote embedding + enrollment overhead.
Expected to add 2–4 s per reference speaker on CPU, much less on GPU.

---

### 3.4: `noise_reduction` pre-processing in realtime workers

**Gap**: G-17 — OpenAI's `noise_reduction: { "type": "near_field" }` applies a pre-VAD
noise filter. Realtime workers don't have this capability.

**Strategy**: Integrate RNNoise (lightweight, runs in real-time on CPU) as an optional
pre-processing step in the realtime worker audio pipeline.

**Realtime worker change**:

```python
class AudioPipeline:
    def __init__(self, noise_reduction: str | None = None):
        self.denoise = RNNoise() if noise_reduction else None

    def process(self, pcm_frame: bytes) -> bytes:
        if self.denoise:
            pcm_frame = self.denoise.process(pcm_frame)
        return self.vad.process(pcm_frame)
```

**Gateway**: Extract `noise_reduction.type` from `session.update` and pass via worker URL:

```python
noise_reduction = session.get("noise_reduction") or session.get("input_audio_noise_reduction")
if noise_reduction:
    session_config["noise_reduction"] = noise_reduction.get("type", "near_field")
```

**Session created / updated**: Echo `noise_reduction` back to client as non-null when active:

```python
"noise_reduction": {"type": session_config["noise_reduction"]} if session_config.get("noise_reduction") else None,
```

**Design Spike Required**: Evaluate RNNoise latency profile under real-time constraints.
RNNoise operates on 10 ms frames at 48 kHz — may require sample rate conversion for
our 16 kHz pipeline.

---

### 3.5: Synchronous batch endpoint scalability

**Gap**: B-7 — Under concurrent load, OpenAI-mode requests hold HTTP connections open
for the full transcription duration. This starves short jobs and can exhaust the
ASGI worker pool.

**Strategy**: Move the polling loop off the HTTP thread pool using FastAPI's
`BackgroundTask` machinery and a per-request `asyncio.Queue` fed by the Redis pub/sub
job-complete event.

Current path:

```
HTTP thread → poll DB every 2s → return when complete
```

Target path:

```
HTTP coroutine → subscribe Redis job_complete channel → await single event → return
```

**File**: `dalston/gateway/services/polling.py`

Replace `wait_for_job_completion()` with a pub/sub-based version:

```python
async def wait_for_job_completion_pubsub(
    db: AsyncSession,
    job: JobModel,
    redis: Redis,
    timeout: float = 300.0,
) -> JobCompletionResult:
    channel = f"dalston:jobs:{job.id}:complete"
    pubsub = redis.pubsub()
    await pubsub.subscribe(channel)
    try:
        async with asyncio.timeout(timeout):
            async for message in pubsub.listen():
                if message["type"] == "message":
                    data = json.loads(message["data"])
                    status = data.get("status")
                    return JobCompletionResult(
                        completed=status == "completed",
                        failed=status == "failed",
                        cancelled=status == "cancelled",
                    )
    except TimeoutError:
        return JobCompletionResult(timed_out=True)
    finally:
        await pubsub.unsubscribe(channel)
```

**Orchestrator**: Publish to `dalston:jobs:{job_id}:complete` when a job reaches a
terminal state (completed, failed, cancelled).

This eliminates the 2-second polling interval and removes all synchronous DB load from
the hot path of OpenAI-mode requests.

---

### Phase 3 Verification

```python
# 3.1: SSE streaming
from openai import OpenAI
client = OpenAI(api_key="dk_test", base_url="http://localhost:8000/v1")
with open("long_audio.mp3", "rb") as f:
    stream = client.audio.transcriptions.create(
        model="whisper-1", file=f, stream=True,
    )
    for event in stream:
        print(event.type, getattr(event, 'delta', None))
# Expected: multiple transcript.text.delta events, then transcript.text.done

# 3.3: Speaker references
curl -X POST http://localhost:8000/v1/audio/transcriptions \
  -H "Authorization: Bearer dk_test" \
  -F "file=@meeting.mp3" -F "model=whisper-1" \
  -F "response_format=diarized_json" \
  -F 'known_speaker_references=[{"name":"Alice","audio":"data:audio/wav;base64,..."}]' \
  | jq '.utterances[] | select(.speaker=="Alice")'

# 3.5: Scalability — verify pub/sub replaces polling
# Under load: 20 concurrent long audio files
# Observe: ASGI thread pool usage does not grow with request count
```

### Phase 3 Checkpoint

- [ ] `stream=true` returns `text/event-stream` with `transcript.text.delta` events
- [ ] SSE stream terminates with `transcript.text.done` event
- [ ] `include=["logprobs"]` adds per-token logprobs to streaming events
- [ ] `known_speaker_references` causes speaker IDs to match provided names
- [ ] Speaker embedding engine integrated into DAG builder
- [ ] `noise_reduction` field accepted in session update and echoed back in session created
- [ ] RNNoise pre-processing applied in realtime worker when `noise_reduction` is set
- [ ] Batch synchronous mode uses Redis pub/sub instead of DB polling
- [ ] OpenAI-mode HTTP threads release immediately once pub/sub event arrives
- [ ] 20 concurrent long-audio requests complete without ASGI pool exhaustion

---

## Full Files Changed

| File | Phase | Change |
|------|-------|--------|
| `dalston/gateway/api/v1/openai_audio.py` | 1 | `diarized_json` enum + formatter, `usage` model, `model` field, `prompt_to_vocabulary()`, `validate_openai_request()` with prompt length |
| `dalston/gateway/api/v1/transcription.py` | 1, 2 | `temperature` fix, prompt vocabulary fix, `chunking_strategy`, `known_speaker_names` |
| `dalston/gateway/api/v1/openai_translation.py` | 1 | `temperature` fix, prompt vocabulary fix |
| `dalston/gateway/api/v1/openai_realtime.py` | 1, 2 | Item events, session created, resample mapping, `turn_detection` params, `noise_reduction` |
| `dalston/gateway/middleware/auth.py` | 1 | `sk-` prefix warning |
| `dalston/gateway/services/rate_limiter.py` | 1 | `reset_after_seconds` in `RateLimitResult` |
| `dalston/gateway/middleware/rate_limit.py` | 1 | `x-ratelimit-*` response headers |
| `dalston/gateway/services/ingestion.py` | 2 | Streaming download with early abort |
| `dalston/common/pipeline_types.py` | 2 | `avg_logprob`, `no_speech_prob`, `compression_ratio`, `tokens` fields |
| `engines/stt-batch-transcribe-faster-whisper-*/engine.py` | 2 | Capture quality signals per segment |
| `engines/stt-batch-transcribe-whisperx-*/engine.py` | 2 | Capture quality signals per segment |
| `engines/stt-merge/final-merger/engine.py` | 2 | Preserve quality fields; apply `known_speaker_names` relabelling |
| `engines/realtime/*/engine.py` | 2, 3 | `client_sample_rate` resampling, VAD param wiring, RNNoise |
| `dalston/engine_sdk/types.py` | 3 | `publish_partial_segment()` |
| `dalston/orchestrator/main.py` | 3 | Publish `job:complete` Redis event on terminal state |
| `dalston/gateway/services/polling.py` | 3 | Pub/sub-based `wait_for_job_completion_pubsub()` |
| `engines/speaker-embed/pyannote-embed/` | 3 | New engine: speaker embedding extractor |
| `engines/stt-diarize-pyannote-*/engine.py` | 3 | Accept speaker enrollment embeddings |
| `docker-compose.yml` | 3 | Add `stt-speaker-embed` service |
| `tests/integration/test_openai_parity.py` | 1, 2, 3 | End-to-end parity test suite using `openai` SDK |

---

## Risks and Mitigations

| Risk | Mitigation |
|------|------------|
| PCM16 resampling degrades transcription quality | Benchmark with real 24 kHz and resampled 24→16 kHz samples; if quality loss is unacceptable, evaluate 24 kHz-capable VAD |
| SSE streaming adds Redis pub/sub fan-out load | Gate behind `DALSTON_FEATURE_SSE_STREAMING=true` flag; benchmark under load before enabling by default |
| `known_speaker_references` adds latency | Run speaker embedding extraction in parallel with TRANSCRIBE stage, not sequentially |
| RNNoise sample rate mismatch (48 kHz vs 16 kHz) | Add explicit rate conversion step; profile to ensure < 1 ms per 10 ms frame on CPU |
| Phase 2 engine schema changes break existing jobs | Add fields as `Optional` with `None` default; merge engine uses `seg.get(field)` not `seg[field]` |
| `prompt_to_vocabulary` changes break existing OpenAI callers | Write tests for the common OpenAI prompt patterns (comma list, prose, single term); fuzz edge cases |

---

## Out of Scope

The following gaps are acknowledged but not scheduled in this milestone:

| Gap | Reason |
|-----|--------|
| G-17 (noise_reduction) full implementation | Included as Phase 3.4 spike only; production hardening is a separate milestone |
| Streaming translations (G-9) design spike | Covered by G-1 SSE infrastructure; translation endpoint added in same pass |
| OpenAI SDK conformance test harness | Tracked as a separate QA milestone |

---

## Unblocked By This Milestone

- Drop-in replacement for `openai.audio.transcriptions.create(stream=True)` workflows
- Telephony clients sending 24 kHz audio (Twilio, Vonage)
- Quality-aware post-processing pipelines that depend on `avg_logprob` / `no_speech_prob`
- Speaker-attributed transcripts with named speakers from voice profiles
- Adaptive backoff in OpenAI SDK clients (via rate-limit headers)
