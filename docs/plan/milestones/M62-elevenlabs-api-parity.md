# M62: ElevenLabs API Parity

| | |
|---|---|
| **Goal** | Close all actionable gaps between Dalston's ElevenLabs-compatible API and the current ElevenLabs ASR spec |
| **Duration** | Phase 1: 2 weeks · Phase 2: 4 weeks · Phase 3: 8 weeks |
| **Dependencies** | M08 (ElevenLabs compat base), M45 (security hardening), M48 (realtime routing) |
| **Deliverable** | The ElevenLabs Python SDK works unchanged against Dalston; no silent failures or behavioural divergence under spec-compliant clients |
| **Status** | Not started |
| **Gap Reference** | [`docs/reports/elevenlabs-api-parity-gap-analysis.md`](../../reports/elevenlabs-api-parity-gap-analysis.md) |

## User Stories

> *"As a developer migrating from ElevenLabs, I can point the ElevenLabs Python SDK at Dalston by changing only the base URL — the same parameters, the same response shape, the same error format."*

> *"As an async caller using `webhook=true`, I receive a push notification when my transcription completes instead of polling forever."*

> *"As a browser application, I can open a real-time WebSocket session using a short-lived token rather than embedding my API key in client-side JavaScript."*

---

## Problem

The original M08 implementation established the structural scaffold for ElevenLabs compatibility.
What remains is a set of fidelity gaps that cause silent failures and observable behavioural
divergence under spec-compliant clients:

```
CRITICAL BUGS (client receives wrong behaviour)
───────────────────────────────────────────────────────────────
  ├── DELETE /transcripts/{id} endpoint missing entirely
  ├── commit_strategy defaults to "vad" — ElevenLabs spec mandates "manual"
  ├── webhook=true returns acknowledgement but never pushes results
  └── GET /transcripts/{id} returns non-spec "processing" object for in-flight jobs

SILENT PARAMETER DROPS (accepted but never applied)
───────────────────────────────────────────────────────────────
  ├── tag_audio_events — accepted, silently ignored
  ├── model_id — silently substituted with default engine
  ├── temperature / seed — not forwarded to engine
  ├── enable_logging — causes 422 on some client versions
  └── keyterm word-count limit (≤5 words) not enforced

RESPONSE SCHEMA GAPS (missing fields)
───────────────────────────────────────────────────────────────
  ├── words[].logprob — never emitted (batch + realtime)
  ├── words[].characters — never emitted (batch only)
  ├── words[].type — hardcoded "word"; "spacing" and "audio_event" absent
  ├── additional_formats — ElevenLabs returns inline; Dalston has separate endpoint
  └── request_id — always null in async responses

REALTIME PROTOCOL GAPS
───────────────────────────────────────────────────────────────
  ├── ?token= auth not supported (blocks browser clients using ElevenLabs SDK)
  ├── include_language_detection not wired
  ├── VAD tuning params discarded (vad_silence_threshold_secs, vad_threshold, …)
  ├── session_started config echo incomplete
  ├── previous_text context hint not forwarded
  ├── ulaw_8000 accepted at handshake, decoded as garbage
  └── 13 ElevenLabs error subtypes collapsed to generic "error"

BLIND SPOTS
───────────────────────────────────────────────────────────────
  ├── No file size limit enforced at gateway (3 GB / 2 GB limits)
  ├── ulaw_8000 not rejected early — produces silent garbage output
  ├── No idempotency key support — retried POSTs create duplicate jobs
  ├── No rate-limit headers on responses
  └── cloud_storage_url provider coverage unverified (Dropbox, Google Drive)
```

---

## Phase 1: Gateway Fidelity (Weeks 1–2)

All changes in this phase are confined to `dalston/gateway/`. No engine, pipeline schema,
or worker changes. Every step is independently deployable and testable in isolation.

---

### 1.1: Fix `commit_strategy` default

**Gap:** G02 — ElevenLabs mandates `"manual"` as the default commit strategy. Dalston defaults
to `"vad"`, which causes automatic commits for any client that does not pass the parameter
explicitly. This is the highest-risk behavioural divergence in the realtime path.

**File:** `dalston/gateway/api/v1/realtime.py:536`

```python
# Before
commit_strategy: Annotated[
    str, Query(description="Commit strategy: 'vad' or 'manual'")
] = "vad",

# After
commit_strategy: Annotated[
    str, Query(description="Commit strategy: 'vad' or 'manual'")
] = "manual",
```

---

### 1.2: Fix GET transcript response for in-progress jobs

**Gap:** G04 — ElevenLabs defines only two outcomes from `GET /transcripts/{id}`: a completed
transcript (200) or not-found (404). Dalston returns a custom `ElevenLabsProcessingResponse`
for in-flight jobs. Strict clients cannot parse it and will surface it as an error or ignore
the body.

The correct pattern: return 404 with a `Retry-After` header while the job is running.
Clients polling for async results will naturally retry. Return 410 Gone for failed or
cancelled jobs so the caller knows not to retry.

**File:** `dalston/gateway/api/v1/speech_to_text.py`

Remove `ElevenLabsProcessingResponse` from the response model and the handler:

```python
# Remove this model entirely
class ElevenLabsProcessingResponse(BaseModel): ...

# Route declaration — remove ElevenLabsProcessingResponse from union
@router.get(
    "/transcripts/{transcription_id}",
    response_model=ElevenLabsTranscript,     # was: | ElevenLabsProcessingResponse
    ...
)

# Handler — replace the status switch with HTTP semantics
if job.status != JobStatus.COMPLETED.value:
    if job.status == JobStatus.FAILED.value:
        raise HTTPException(
            status_code=500,
            detail=Err.TRANSCRIPTION_FAILED.format(error=job.error or "Unknown error"),
        )
    if job.status in (JobStatus.CANCELLED.value, JobStatus.CANCELLING.value):
        raise HTTPException(status_code=410, detail=Err.TRANSCRIPTION_CANCELLED)
    # PENDING or RUNNING — not ready yet
    raise HTTPException(
        status_code=404,
        detail=Err.TRANSCRIPTION_NOT_FOUND,
        headers={"Retry-After": "5"},
    )
```

---

### 1.3: Implement DELETE /transcripts/{transcription_id}

**Gap:** G01 — The DELETE endpoint is completely absent. Any ElevenLabs client that calls
DELETE receives 405 Method Not Allowed. The endpoint must perform a soft-delete (set
`deleted_at`) and schedule async S3 artifact removal.

**File:** `dalston/gateway/api/v1/speech_to_text.py`

Add after the GET handler:

```python
@router.delete(
    "/transcripts/{transcription_id}",
    status_code=200,
    summary="Delete transcript (ElevenLabs compatible)",
    responses={
        200: {"description": "Transcript deleted successfully"},
        404: {"description": "Transcript not found"},
    },
)
async def delete_transcript(
    transcription_id: UUID,
    principal: Annotated[Principal, Depends(get_principal)],
    security_manager: Annotated[SecurityManager, Depends(get_security_manager)],
    db: AsyncSession = Depends(get_db),
    jobs_service: JobsService = Depends(get_jobs_service),
    storage: StorageService = Depends(get_storage_service),
    redis: Redis = Depends(get_redis),
) -> dict:
    """Delete a transcript and its associated audio artifacts."""
    job = await jobs_service.get_job_authorized(
        db, transcription_id, principal, security_manager
    )
    if job is None:
        raise HTTPException(status_code=404, detail=Err.TRANSCRIPTION_NOT_FOUND)

    security_manager.require_permission(principal, Permission.JOB_DELETE)

    await jobs_service.soft_delete_job(db, job.id)
    await storage.schedule_artifact_deletion(job.id, redis)

    return {}
```

`JobsService.soft_delete_job` sets `deleted_at = now()` on the job row.
`StorageService.schedule_artifact_deletion` publishes a Redis message consumed by a
background worker that removes the S3 objects asynchronously (audio file + transcript JSON).
Deletion is non-blocking from the client's perspective.

Extend `get_job_authorized` to filter out soft-deleted jobs (treat as 404), and add a
`JOB_DELETE` permission.

---

### 1.4: Populate `request_id` in async response

**Gap:** G22 — `ElevenLabsAsyncResponse.request_id` is always `None`. The gateway
already extracts `X-Request-ID` into `request.state.request_id` and echoes it in response
headers. The async response should carry the same value.

**File:** `dalston/gateway/api/v1/speech_to_text.py:307`

```python
# Before
return ElevenLabsAsyncResponse(
    message="Request processed successfully",
    transcription_id=str(job.id),
)

# After
return ElevenLabsAsyncResponse(
    message="Request processed successfully",
    request_id=getattr(request.state, "request_id", None),
    transcription_id=str(job.id),
)
```

---

### 1.5: Enforce keyterm word-count limit

**Gap:** G21 — ElevenLabs rejects keyterms with more than 5 words. Dalston validates
character length (≤50) and count (≤100) but not the word limit. Clients submitting
multi-word terms that ElevenLabs rejects will be accepted silently by Dalston.

**File:** `dalston/gateway/api/v1/speech_to_text.py` (batch validation loop) and
`dalston/gateway/api/v1/realtime.py` (WebSocket validation loop)

In both locations, add after the character-length check:

```python
word_count = len(term.split())
if word_count > 5:
    raise HTTPException(
        status_code=400,
        detail=Err.KEYTERM_TOO_MANY_WORDS.format(count=word_count),
    )
```

Add to `dalston/gateway/error_codes.py`:

```python
KEYTERM_TOO_MANY_WORDS = "Each keyterm must be at most 5 words, got {count}"
```

The WebSocket path raises a JSON error message and closes the connection, consistent with
existing keyterm error handling there.

---

### 1.6: Accept `enable_logging` without error

**Gap:** G26 — ElevenLabs clients pass `enable_logging=false` for enterprise zero-retention
mode. Dalston does not define this parameter; some FastAPI versions will return a 422
Unprocessable Entity because the field is unexpected in a strict `multipart/form-data`
validation context.

For a self-hosted deployment, retention is operator-controlled. Accept the parameter
and ignore it. Document the limitation.

**File:** `dalston/gateway/api/v1/speech_to_text.py` — add form field:

```python
enable_logging: Annotated[
    bool,
    Form(description="Retention control (accepted for ElevenLabs compatibility; ignored — "
                     "retention is operator-controlled in self-hosted deployments)"),
] = True,
```

**File:** `dalston/gateway/api/v1/realtime.py` — add query param:

```python
enable_logging: Annotated[
    bool, Query(description="Accepted for ElevenLabs compatibility; ignored.")
] = True,
```

---

### 1.7: Reject unsupported `audio_format` at WebSocket handshake

**Gap:** B10 — Dalston accepts `audio_format=ulaw_8000` at handshake (because the parameter
has no whitelist) but then feeds raw μ-law bytes to the worker as if they were PCM. The
worker produces garbage transcription with no error. The session should be rejected
immediately with a clear message.

**File:** `dalston/gateway/api/v1/realtime.py`

Define a supported format set before the handler:

```python
_SUPPORTED_AUDIO_FORMATS: frozenset[str] = frozenset({
    "pcm_8000",
    "pcm_16000",
    "pcm_22050",
    "pcm_24000",
    "pcm_44100",
    "pcm_48000",
})
```

Add validation before accepting the connection:

```python
if audio_format not in _SUPPORTED_AUDIO_FORMATS:
    await websocket.accept()
    await websocket.send_json({
        "message_type": "input_error",
        "error": (
            f"Unsupported audio_format '{audio_format}'. "
            f"Supported formats: {sorted(_SUPPORTED_AUDIO_FORMATS)}"
        ),
    })
    await websocket.close(code=WS_CLOSE_INVALID_REQUEST, reason="Unsupported audio format")
    return
```

Note: `ulaw_8000` support is deferred to step 2.7. Once μ-law decoding is implemented,
add it to the frozenset and remove from the rejection path.

---

### 1.8: Complete `session_started` config echo

**Gap:** G13 — ElevenLabs echoes the full effective session config in `session_started` so
clients can verify active settings. Dalston omits `include_timestamps`,
`include_language_detection`, `enable_logging`, and the four VAD tuning fields.

**File:** `dalston/gateway/api/v1/realtime.py:684`

```python
# Before
await websocket.send_json({
    "message_type": "session_started",
    "session_id": allocation.session_id,
    "config": {
        "sample_rate": sample_rate,
        "audio_format": audio_format,
        "language_code": language_code,
        "model_id": model_id,
        "commit_strategy": commit_strategy,
    },
})

# After
await websocket.send_json({
    "message_type": "session_started",
    "session_id": allocation.session_id,
    "config": {
        "sample_rate": sample_rate,
        "audio_format": audio_format,
        "language_code": language_code,
        "model_id": model_id,
        "commit_strategy": commit_strategy,
        "include_timestamps": include_timestamps,
        "include_language_detection": include_language_detection,  # wired in 1.9
        "enable_logging": enable_logging,                          # added in 1.6
        # VAD params — values reflect defaults until 2.5 wires them to the engine
        "vad_silence_threshold_secs": vad_silence_threshold_secs,
        "vad_threshold": vad_threshold,
        "min_speech_duration_ms": min_speech_duration_ms,
        "min_silence_duration_ms": min_silence_duration_ms,
    },
})
```

The VAD parameters are accepted as query params in step 1.8 but not forwarded to the
engine until step 2.5. Adding them to the echo now means the client at least sees what
values it sent.

Add the four VAD params as query params on the handler (mirrors 1.6 pattern):

```python
vad_silence_threshold_secs: Annotated[
    float, Query(description="Silence duration (s) before VAD commits")
] = 1.5,
vad_threshold: Annotated[
    float, Query(description="VAD speech detection sensitivity")
] = 0.4,
min_speech_duration_ms: Annotated[
    int, Query(description="Minimum speech segment length in ms")
] = 100,
min_silence_duration_ms: Annotated[
    int, Query(description="Minimum silence segment length in ms")
] = 100,
```

---

### 1.9: Wire `include_language_detection`

**Gap:** G14 — `include_language_detection` controls whether the detected language appears
in `committed_transcript` and `committed_transcript_with_timestamps` messages. The realtime
worker already detects language and includes it in `transcript.final` messages. This is a
pure formatting change in the gateway translation layer.

**File:** `dalston/gateway/api/v1/realtime.py`

Add query param to the handler:

```python
include_language_detection: Annotated[
    bool, Query(description="Include language_code in committed transcript messages")
] = False,
```

Pass to `_proxy_to_worker_elevenlabs`:

```python
await _proxy_to_worker_elevenlabs(
    ...
    word_timestamps=include_timestamps,
    include_language_detection=include_language_detection,  # ADD
    vocabulary=parsed_vocabulary,
)
```

In `_elevenlabs_worker_to_client`, update the `transcript.final` branch:

```python
elif msg_type == "transcript.final":
    if include_timestamps and data.get("words"):
        translated = {
            "message_type": "committed_transcript_with_timestamps",
            "text": data.get("text", ""),
            "words": [...],
        }
    else:
        translated = {
            "message_type": "committed_transcript",
            "text": data.get("text", ""),
        }
    # Include language if requested (data is already present from worker)
    if include_language_detection and data.get("language"):
        translated["language_code"] = data["language"]
```

---

### 1.10: Map worker errors to ElevenLabs error type vocabulary

**Gap:** G15 — ElevenLabs defines 13 structured error `message_type` values. Dalston emits
a single generic `"error"` type. Clients that branch on error type for retry logic or UX
messaging cannot distinguish conditions.

**File:** `dalston/gateway/api/v1/realtime.py`

Add a mapping constant before the handler:

```python
_WORKER_ERROR_TO_ELEVENLABS: dict[str, str] = {
    "auth_failed":            "auth_error",
    "rate_limited":           "rate_limited",
    "quota_exceeded":         "quota_exceeded",
    "no_capacity":            "queue_overflow",
    "capacity_exhausted":     "resource_exhausted",
    "session_time_exceeded":  "session_time_limit_exceeded",
    "input_invalid":          "input_error",
    "chunk_too_large":        "chunk_size_exceeded",
    "no_speech":              "insufficient_audio_activity",
    "transcriber_failed":     "transcriber_error",
    "lag_exceeded":           "transcriber_error",
}
```

In `_elevenlabs_worker_to_client`, update the error branch:

```python
elif msg_type == "error":
    worker_code = data.get("code", "")
    el_type = _WORKER_ERROR_TO_ELEVENLABS.get(worker_code, "transcriber_error")
    translated = {
        "message_type": el_type,
        "error": data.get("message", "Unknown error"),
    }
```

Also map errors raised by the gateway itself before the worker is contacted. In the capacity
check:

```python
await websocket.send_json({
    "message_type": "queue_overflow",
    "error": "No realtime capacity available",
})
```

---

### 1.11: Synthesise `spacing` word tokens

**Gap:** G06 (partial) — ElevenLabs emits `spacing` tokens representing pauses between
words. These are used by subtitle renderers and transcription editors. Dalston hardcodes
`type="word"` everywhere. Spacing tokens can be synthesised from timestamp gaps with no
engine changes.

**File:** `dalston/gateway/api/v1/speech_to_text.py`

Add a helper and update `_format_elevenlabs_response`:

```python
_SPACING_THRESHOLD_SECS = 0.1  # Gaps wider than 100 ms become spacing tokens


def _with_spacing_tokens(words: list[ElevenLabsWord]) -> list[ElevenLabsWord]:
    """Insert spacing tokens between words with meaningful gaps."""
    if not words:
        return words
    result: list[ElevenLabsWord] = []
    for i, word in enumerate(words):
        result.append(word)
        if i < len(words) - 1:
            gap = words[i + 1].start - word.end
            if gap >= _SPACING_THRESHOLD_SECS:
                result.append(
                    ElevenLabsWord(
                        text=" ",
                        start=word.end,
                        end=words[i + 1].start,
                        type="spacing",
                        speaker_id=None,
                    )
                )
    return result
```

Call in `_format_elevenlabs_response` before building the return value:

```python
if words:
    words = _with_spacing_tokens(words)
```

Update `ElevenLabsWord.type` to drop the hardcoded default:

```python
class ElevenLabsWord(BaseModel):
    text: str
    start: float
    end: float
    type: str = "word"          # remains "word"; spacing tokens override explicitly
    speaker_id: str | None = None
    logprob: float | None = None    # populated in Phase 2
```

---

### 1.12: Inline `additional_formats` via ExportService

**Gap:** G17 — ElevenLabs returns requested export formats inline in the POST response
under `additional_formats`. Dalston has a separate export endpoint (not in the ElevenLabs
spec). Clients that pass `additional_formats` receive nothing.

**File:** `dalston/gateway/api/v1/speech_to_text.py`

Add the request parameter:

```python
additional_formats: Annotated[
    str | None,
    Form(
        description='JSON array of export formats, e.g. \'["srt","txt"]\'. '
                    "Supported: srt, webvtt, txt, json",
    ),
] = None,
```

Add an output model:

```python
class ElevenLabsAdditionalFormat(BaseModel):
    requested_format: str
    file_extension: str
    content_type: str
    is_base64_encoded: bool = False
    content: str


class ElevenLabsTranscript(BaseModel):
    language_code: str | None = None
    language_probability: float | None = None
    text: str
    words: list[ElevenLabsWord] | None = None
    transcription_id: str
    additional_formats: list[ElevenLabsAdditionalFormat] | None = None  # ADD
```

Parse and generate in `_format_elevenlabs_response` (pass `export_service` and
`requested_formats: list[str] | None` as arguments):

```python
inline_formats: list[ElevenLabsAdditionalFormat] | None = None
if requested_formats:
    inline_formats = []
    for fmt in requested_formats:
        export_format = export_service.validate_format(fmt, strict=False)
        if export_format is None:
            continue
        content = export_service.render_to_string(transcript, export_format)
        inline_formats.append(
            ElevenLabsAdditionalFormat(
                requested_format=fmt,
                file_extension=export_format.file_extension,
                content_type=export_format.content_type,
                is_base64_encoded=False,
                content=content,
            )
        )
```

`ExportService.render_to_string` is a new method that returns the export as a string
instead of an HTTP `Response`. It reuses the existing rendering logic.

---

## Phase 2: Pipeline Integration (Weeks 3–6)

Changes in this phase cross the gateway boundary into engines, the realtime SDK, or the
pipeline parameter schema. Each step requires coordinated changes across at least two
components.

---

### 2.1: Thread `logprob` from engine output

**Gap:** G07 — faster-whisper and WhisperX both produce per-word log probability scores.
The data exists in engine output but is discarded before the transcript reaches S3, so it
cannot be surfaced in the API response.

**Part A — Merge engine** (`engines/stt-merge/final-merger/engine.py`)

When assembling `transcript.json`, preserve `logprob` from each word in the source stage
output:

```python
word_entry = {
    "text": w.get("word", w.get("text", "")),
    "start": w.get("start", 0),
    "end": w.get("end", 0),
    "logprob": w.get("probability") or w.get("logprob"),  # faster-whisper: probability
}
```

**Part B — Common pipeline types** (`dalston/common/pipeline_types.py`)

Add `logprob: float | None = None` to the `Word` model.

**Part C — Gateway** (`dalston/gateway/api/v1/speech_to_text.py`)

In `_format_elevenlabs_response`, populate from the word dict:

```python
ElevenLabsWord(
    ...
    logprob=w.get("logprob"),
)
```

**Part D — Realtime** (`dalston/gateway/api/v1/realtime.py`)

In `_elevenlabs_worker_to_client`, add `logprob` to the word list in
`committed_transcript_with_timestamps`:

```python
{
    "text": w.get("word", ""),
    "start": w.get("start", 0),
    "end": w.get("end", 0),
    "type": "word",
    "logprob": w.get("logprob"),
}
```

---

### 2.2: Character-level timestamps

**Gaps:** G10, G11 — ElevenLabs returns character-level `characters` arrays on each word
when `timestamps_granularity="character"`. WhisperX's alignment stage produces
character-level timing as an optional output; the data is currently discarded.

**Part A — Align engine** (e.g. `engines/stt-align/whisperx-align/engine.py`)

Check whether `model.align()` returns `char_segments`. If yes, add them to the output
word dict:

```python
word_entry["characters"] = [
    {"text": c["char"], "start": c["start"], "end": c["end"]}
    for c in char_segments
    if "start" in c
]
```

If the alignment model does not produce character data, skip the field.

**Part B — Batch endpoint** (`dalston/gateway/api/v1/speech_to_text.py`)

Change `map_timestamps_granularity` to pass `"character"` through when the data is
available, or raise 422 when it is not:

```python
def map_timestamps_granularity(granularity: str, char_supported: bool) -> str:
    if granularity == "character":
        if not char_supported:
            raise HTTPException(
                status_code=422,
                detail="timestamps_granularity='character' is not supported by the "
                       "active transcription engine. Use 'word' or 'none'.",
            )
        return "character"
    return {"none": "none", "word": "word"}.get(granularity, "word")
```

**Part C — ElevenLabsWord model**

```python
class ElevenLabsCharacter(BaseModel):
    text: str
    start: float
    end: float


class ElevenLabsWord(BaseModel):
    text: str
    start: float
    end: float
    type: str = "word"
    speaker_id: str | None = None
    logprob: float | None = None
    characters: list[ElevenLabsCharacter] | None = None   # ADD
```

Populate in `_format_elevenlabs_response` from the word dict's `characters` field.

---

### 2.3: Diarization threshold

**Gap:** G16 — ElevenLabs exposes `diarization_threshold` (~0.22 default) for controlling
speaker separation sensitivity when the number of speakers is unknown. pyannote's
`SpeakerDiarization` pipeline accepts an equivalent `clustering_threshold` parameter.

**Part A — Batch endpoint** (`dalston/gateway/api/v1/speech_to_text.py`)

Add form field:

```python
diarization_threshold: Annotated[
    float | None,
    Form(
        description="Speaker separation sensitivity (0.0–1.0). "
                    "Only applies when diarize=true and num_speakers is unset.",
        ge=0.0, le=1.0,
    ),
] = None,
```

Add to parameters dict only when relevant:

```python
if diarize and num_speakers is None and diarization_threshold is not None:
    parameters["diarization_threshold"] = diarization_threshold
```

**Part B — Diarize engine** (`engines/stt-diarize/pyannote-*/engine.py`)

Read and apply the parameter:

```python
threshold = task_input.parameters.get("diarization_threshold")
pipeline_params = {}
if threshold is not None:
    pipeline_params["clustering"] = {"threshold": threshold}
diarization = pipeline(audio_path, **pipeline_params)
```

---

### 2.4: Forward `temperature` and `seed`

**Gap:** G20 — faster-whisper accepts `temperature` (float or list of floats) and
`repetition_penalty`. Neither is forwarded from the ElevenLabs batch endpoint.

**Part A — Batch endpoint** (`dalston/gateway/api/v1/speech_to_text.py`)

Add form fields:

```python
temperature: Annotated[
    float | None,
    Form(description="Sampling temperature (0.0–2.0)", ge=0.0, le=2.0),
] = None,
seed: Annotated[
    int | None,
    Form(description="Random seed for deterministic output", ge=0, le=2_147_483_647),
] = None,
```

Add to parameters dict unconditionally (including `temperature=0`):

```python
if temperature is not None:
    parameters["temperature"] = temperature
if seed is not None:
    parameters["seed"] = seed
```

**Part B — Transcribe engine** (`engines/stt-transcribe/*/engine.py`)

Read from task parameters:

```python
temperature = task_input.parameters.get("temperature", 0)
# faster-whisper accepts a float or a tuple of fallback values
model.transcribe(audio_path, temperature=temperature, ...)
```

Seed support in faster-whisper is indirect (via `torch.manual_seed`). Apply if present:

```python
seed = task_input.parameters.get("seed")
if seed is not None:
    import torch
    torch.manual_seed(seed)
```

---

### 2.5: VAD tuning parameters in realtime

**Gap:** G12 — ElevenLabs exposes `vad_silence_threshold_secs`, `vad_threshold`,
`min_speech_duration_ms`, `min_silence_duration_ms` as connection-time parameters.
Dalston accepts them (after step 1.8) but does not forward them to the worker.

**Part A — Gateway** (`dalston/gateway/api/v1/realtime.py`)

Pass to `_proxy_to_worker_elevenlabs`:

```python
await _proxy_to_worker_elevenlabs(
    ...
    vad_silence_threshold_secs=vad_silence_threshold_secs,
    vad_threshold=vad_threshold,
    min_speech_duration_ms=min_speech_duration_ms,
    min_silence_duration_ms=min_silence_duration_ms,
)
```

In `_proxy_to_worker_elevenlabs`, add to the worker URL params:

```python
params["vad_silence_threshold_secs"] = str(vad_silence_threshold_secs)
params["vad_threshold"] = str(vad_threshold)
params["min_speech_duration_ms"] = str(min_speech_duration_ms)
params["min_silence_duration_ms"] = str(min_silence_duration_ms)
```

**Part B — Realtime engine** (`engines/realtime/whisper-streaming/engine.py`)

Read from session URL params and apply to the VAD configuration:

```python
vad_params = {
    "threshold":              float(params.get("vad_threshold", 0.4)),
    "min_speech_duration_ms": int(params.get("min_speech_duration_ms", 100)),
    "min_silence_duration_ms": int(params.get("min_silence_duration_ms", 100)),
}
silence_threshold_secs = float(params.get("vad_silence_threshold_secs", 1.5))
```

Pass to the Silero VAD model and the silence-based commit loop.

**Part C — Realtime SDK** (`dalston/realtime_sdk/`)

If the SDK provides a session configuration type, add the four new fields to it.

---

### 2.6: Forward `previous_text` context hint

**Gap:** G23 — ElevenLabs allows the client to send `previous_text` in the first
`input_audio_chunk` to prime the transcription context (equivalent to Whisper's
`initial_prompt`). The field is currently parsed but silently discarded.

**Part A — Gateway** (`dalston/gateway/api/v1/realtime.py`)

In `_elevenlabs_client_to_worker`, capture `previous_text` from the first chunk and
forward it as a worker message before the first audio frame:

```python
first_chunk = True

if msg_type == "input_audio_chunk":
    if first_chunk:
        first_chunk = False
        previous_text = data.get("previous_text")
        if previous_text:
            await worker_ws.send(json.dumps({
                "type": "initial_prompt",
                "text": previous_text,
            }))
    # then send audio bytes as before
```

**Part B — Realtime engine**

Handle `initial_prompt` message type: set it as the Whisper `initial_prompt` parameter
on the next transcription call.

---

### 2.7: μ-law audio decoding

**Gap:** G25 — `ulaw_8000` is a standard telephony encoding (G.711). After step 1.7
rejects it at handshake, this step adds proper decoding support and removes the rejection.

**Part A — Gateway** (`dalston/gateway/api/v1/realtime.py`)

Add `"ulaw_8000"` to `_SUPPORTED_AUDIO_FORMATS`.

In `_elevenlabs_client_to_worker`, detect μ-law sessions and transcode before forwarding:

```python
# At session start, determine if decoding is needed
is_ulaw = audio_format == "ulaw_8000"

if msg_type == "input_audio_chunk":
    audio_bytes = base64.b64decode(data.get("audio_base_64", ""))
    if is_ulaw and audio_bytes:
        # audioop is stdlib; converts 8-bit μ-law to 16-bit linear PCM
        import audioop
        audio_bytes = audioop.ulaw2lin(audio_bytes, 2)
    if audio_bytes:
        await worker_ws.send(audio_bytes)
```

The worker receives standard PCM regardless of the client's encoding.

**Note:** `audioop` was deprecated in Python 3.11 and removed in 3.13. If the runtime is
3.13+, use `soundfile` or an equivalent library for μ-law decoding. Abstract into a
`_decode_audio(data: bytes, fmt: str) -> bytes` helper.

---

### 2.8: Model ID routing or explicit rejection

**Gap:** G08 — Dalston silently ignores `model_id` and uses `settings.default_model`.
Clients selecting `scribe_v1` for low-latency or `scribe_v2` for accuracy get the same
engine regardless. The substitution is invisible.

**File:** `dalston/config.py`

Add a model mapping to settings:

```python
class Settings(BaseSettings):
    ...
    elevenlabs_model_map: dict[str, str] = Field(
        default_factory=lambda: {
            "scribe_v1": "",    # empty → use default_model
            "scribe_v2": "",    # empty → use default_model
        },
        description="Map ElevenLabs model IDs to Dalston engine names. "
                    "Empty string means use default_model.",
    )
```

**File:** `dalston/gateway/api/v1/speech_to_text.py`

Replace the silent ignore with a lookup:

```python
def resolve_engine_for_model_id(model_id: str, settings: Settings) -> str:
    mapped = settings.elevenlabs_model_map.get(model_id)
    if mapped is None:
        # Unknown model ID — reject explicitly
        raise HTTPException(
            status_code=422,
            detail=f"Unknown model_id '{model_id}'. "
                   f"Supported: {list(settings.elevenlabs_model_map.keys())}",
        )
    return mapped or settings.default_model
```

Apply in the parameter building section, replacing the current silent fallback.
Operators can set `DALSTON_ELEVENLABS_MODEL_MAP__scribe_v2=faster-whisper-large-v3`
to route scribe_v2 requests to a higher-quality engine.

---

## Phase 3: New Capabilities (Weeks 7–14)

Changes in this phase require new infrastructure, new subsystems, or significant pipeline
extensions. Each step should be treated as a mini-project with its own design review.

---

### 3.1: File size limits at the gateway

**Gap:** B05 — ElevenLabs enforces 3 GB for file uploads and 2 GB for `cloud_storage_url`.
Dalston has no gateway-level enforcement; oversized uploads stream fully to S3 before any
limit is applied, wasting bandwidth and storage.

**File:** `dalston/gateway/services/ingestion.py`

Add limits as constants:

```python
MAX_UPLOAD_BYTES   = 3 * 1024 ** 3   # 3 GB
MAX_URL_BYTES      = 2 * 1024 ** 3   # 2 GB
```

For file uploads, check `Content-Length` before streaming:

```python
content_length = request.headers.get("content-length")
if content_length and int(content_length) > MAX_UPLOAD_BYTES:
    raise HTTPException(
        status_code=413,
        detail=f"File too large. Maximum is {MAX_UPLOAD_BYTES // 1024**3} GB.",
    )
```

Stream with a byte counter and abort if the limit is exceeded mid-upload:

```python
received = 0
async for chunk in file.stream():
    received += len(chunk)
    if received > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="File too large.")
    yield chunk
```

For `cloud_storage_url`, issue a HEAD request before downloading and check
`Content-Length`. If the server does not return `Content-Length`, enforce the limit via
the streaming counter during download (same pattern).

---

### 3.2: Rate-limit response headers

**Gap:** B07 — ElevenLabs clients use `x-ratelimit-*` headers for adaptive backoff.
Dalston enforces rate limits internally but does not expose them in response headers,
so clients fall back to blind exponential backoff.

**File:** `dalston/gateway/services/rate_limiter.py`

Extend `RateLimitResult` to carry header values:

```python
@dataclass
class RateLimitResult:
    allowed: bool
    limit: int
    remaining: int
    reset_after_seconds: float    # ADD
```

**File:** `dalston/gateway/middleware/` (wherever 429 responses are built)

Return headers on both 429 responses and successful responses:

```python
headers = {
    "x-ratelimit-limit-requests": str(result.limit),
    "x-ratelimit-remaining-requests": str(result.remaining),
    "x-ratelimit-reset-requests": f"{result.reset_after_seconds:.3f}s",
}
# On 429, also add Retry-After
if not result.allowed:
    headers["retry-after"] = str(int(result.reset_after_seconds) + 1)
```

Inject via a response middleware or by adding headers directly in the dependency.

---

### 3.3: Idempotency key support

**Gap:** B06 — If a client retries a timed-out POST (e.g. after a slow upload), Dalston
creates a duplicate job. ElevenLabs does not document an `Idempotency-Key` header, but the
principle applies: retried requests should be safe.

Implement `Idempotency-Key` as a standard HTTP header. Store the key → response mapping in
Redis with a TTL (24 hours). On a duplicate key, return the cached response immediately.

**File:** `dalston/gateway/middleware/idempotency.py` (new file)

```python
IDEMPOTENCY_TTL_SECS = 86_400  # 24 hours
KEY_PREFIX = "idempotency:"


async def get_cached_response(redis: Redis, key: str) -> dict | None:
    raw = await redis.get(f"{KEY_PREFIX}{key}")
    return json.loads(raw) if raw else None


async def cache_response(redis: Redis, key: str, response: dict) -> None:
    await redis.setex(f"{KEY_PREFIX}{key}", IDEMPOTENCY_TTL_SECS, json.dumps(response))
```

Apply in `create_transcription` before job creation:

```python
idempotency_key = request.headers.get("Idempotency-Key")
if idempotency_key:
    cached = await get_cached_response(redis, idempotency_key)
    if cached:
        return cached   # return the original response verbatim

# ... create job ...

if idempotency_key:
    await cache_response(redis, idempotency_key, result.model_dump())
```

Scope idempotency keys per tenant to prevent cross-tenant collisions.

---

### 3.4: Single-use WebSocket token endpoint

**Gaps:** G05, B01 — Browser-based clients using the ElevenLabs JavaScript SDK cannot
safely embed API keys. ElevenLabs provides a token endpoint for short-lived connection
tokens. Without it, there is no secure way to use the realtime endpoint from a browser.

**New endpoint:** `POST /v1/speech-to-text/realtime/token`

```python
@router.post("/realtime/token", response_model=RealtimeTokenResponse)
async def create_realtime_token(
    principal: Annotated[Principal, Depends(get_principal)],
    security_manager: Annotated[SecurityManager, Depends(get_security_manager)],
    redis: Redis = Depends(get_redis),
) -> RealtimeTokenResponse:
    """Issue a single-use, short-lived token for WebSocket authentication."""
    security_manager.require_permission(principal, Permission.REALTIME_CONNECT)
    token = secrets.token_urlsafe(32)
    await redis.setex(
        f"rt_token:{token}",
        300,   # 5 minutes
        json.dumps({"tenant_id": str(principal.tenant_id), "used": False}),
    )
    return RealtimeTokenResponse(token=token, expires_in=300)
```

**WebSocket handler:** accept `?token=` in addition to `?api_key=` and `xi-api-key`:

```python
# In authenticate_websocket()
token = websocket.query_params.get("token")
if token:
    raw = await redis.get(f"rt_token:{token}")
    if not raw:
        # Token missing or expired
        return None
    data = json.loads(raw)
    if data["used"]:
        # Already consumed — reject
        return None
    # Mark consumed (atomic with SET NX)
    await redis.setex(f"rt_token:{token}", 300, json.dumps({**data, "used": True}))
    return await auth_service.get_tenant(data["tenant_id"])
```

Tokens are single-use, 5-minute TTL, scoped to the issuing tenant.

---

### 3.5: Outbound webhook push delivery

**Gap:** G03 — `webhook=true` currently returns an acknowledgement but never delivers
results. ElevenLabs clients using async mode for long-form audio receive the
`transcription_id` and wait forever for a callback.

This requires a webhook delivery subsystem with four components:

**Component A — Webhook endpoint registry**

New table: `webhook_endpoints(id, tenant_id, url, secret, events[], created_at, active)`.

New REST API (separate from the ElevenLabs compat layer):

- `POST /v1/webhooks` — register an endpoint
- `GET /v1/webhooks` — list
- `DELETE /v1/webhooks/{id}` — remove

**Component B — Delivery worker**

A background worker that subscribes to a Redis queue (`dalston:webhooks:delivery`).
When a job completes, the orchestrator publishes a delivery task:

```json
{
  "event": "transcription.completed",
  "tenant_id": "...",
  "transcription_id": "...",
  "webhook_id": "...",      // optional, routes to a specific endpoint
  "metadata": {...}         // webhook_metadata passthrough
}
```

The worker fetches the transcript, signs the payload with HMAC-SHA256 using the endpoint
secret, and POSTs to the registered URL.

**Component C — Retry policy**

Exponential backoff: attempt at 0 s, 5 s, 30 s, 5 min, 30 min, 2 h. After six failures,
mark the delivery as permanently failed and emit a log event. Use Redis sorted sets keyed
by next-attempt timestamp for scheduling.

**Component D — Gateway integration**

In `create_transcription`, when `webhook=true`, also accept and store `webhook_id`
and `webhook_metadata` as job parameters. When the orchestrator emits `job.completed`,
include these parameters in the delivery task.

`webhook_id` routes to a specific registered endpoint. If absent, deliver to all active
endpoints for the tenant.

---

### 3.6: Multi-channel transcription

**Gap:** G18 — ElevenLabs supports independent per-channel transcription for stereo and
multi-track audio (up to 5 channels), returning a `MultichannelSpeechToTextResponseModel`.
This requires a new pipeline stage and a new response model.

**Stage A — Audio channel splitter** (`engines/stt-prepare/channel-splitter/`)

A new engine that runs after `PREPARE` when `use_multi_channel=true`. Splits the input
audio into N mono files (one per channel) and outputs a manifest:

```json
{
  "channels": [
    {"channel_index": 0, "audio_uri": "s3://.../channel_0.wav"},
    {"channel_index": 1, "audio_uri": "s3://.../channel_1.wav"}
  ]
}
```

**Stage B — Orchestrator fan-out**

The orchestrator detects the multi-channel manifest and creates a `TRANSCRIBE` task per
channel, each tagged with its `channel_index`. Downstream `ALIGN` and `DIARIZE` tasks
similarly fan out per channel.

**Stage C — Merge engine extension**

When all per-channel tasks complete, assemble the `MultichannelSpeechToTextResponseModel`:

```json
{
  "transcripts": [
    { "channel_index": 0, "text": "...", "words": [...], "transcription_id": "..." },
    { "channel_index": 1, "text": "...", "words": [...], "transcription_id": "..." }
  ],
  "transcription_id": "..."
}
```

**Stage D — Gateway**

Accept `use_multi_channel: bool = False` as a form field. Set `parameters["multi_channel"] = True`
and return `MultichannelSpeechToTextResponseModel` when the transcript has a `channels` key.

---

### 3.7: Entity detection annotations

**Gap:** G19 — ElevenLabs returns character-position entity annotations (`pii`, `phi`,
`pci`, `offensive_language`) in the response body. Dalston's `PII_DETECT` stage is
oriented toward audio redaction, not inline annotation.

**Part A — PII_DETECT stage extension**

When `entity_detection` is set and audio redaction is not requested, run the NER model
in annotation-only mode and emit a JSON manifest alongside the redacted audio:

```json
{
  "entities": [
    {"text": "John Smith", "entity_type": "pii", "start_char": 42, "end_char": 52}
  ]
}
```

Support the `entity_detection` filter: `"all"` or an array of specific types
(`["pii", "phi"]`).

**Part B — Merge engine**

Include the `entities` array in `transcript.json`.

**Part C — Gateway response**

Add `entities: list[ElevenLabsEntity] | None = None` to `ElevenLabsTranscript` and
populate from the transcript.

```python
class ElevenLabsEntity(BaseModel):
    text: str
    entity_type: str
    start_char: int
    end_char: int
```

---

## Verification

### Phase 1 Acceptance

```python
from elevenlabs import ElevenLabs

client = ElevenLabs(api_key="dalston-key", base_url="http://localhost:8000")

# Sync transcription
result = client.speech_to_text.convert(
    file=open("sample.mp3", "rb"),
    model_id="scribe_v1",
    diarize=True,
    timestamps_granularity="word",
)
assert result.transcription_id
assert result.text
assert all(w.type in ("word", "spacing") for w in result.words)

# Async transcription
result = client.speech_to_text.convert(
    file=open("sample.mp3", "rb"),
    model_id="scribe_v1",
    webhook=True,
)
assert result.transcription_id
assert result.request_id is not None   # populated after 1.4

# Delete transcript
client.speech_to_text.delete(transcription_id=result.transcription_id)

# Verify deleted
try:
    client.speech_to_text.get(transcription_id=result.transcription_id)
    assert False, "Should have raised"
except ElevenLabsError as e:
    assert e.status_code == 404
```

### Phase 1 Realtime Acceptance

```python
async with client.speech_to_text.realtime(model_id="scribe_v1") as session:
    # commit_strategy defaults to "manual" — no automatic commits
    started = await session.__anext__()
    assert started.message_type == "session_started"
    assert started.config.commit_strategy == "manual"
    assert "include_timestamps" in started.config.__fields__

    await session.send_audio(chunk, commit=True)
    msg = await session.__anext__()
    assert msg.message_type == "committed_transcript"
```

### Phase 2 Acceptance

```python
# logprob present on words
result = client.speech_to_text.convert(file=open("sample.mp3", "rb"), model_id="scribe_v1")
assert all(w.logprob is not None for w in result.words)

# character timestamps
result = client.speech_to_text.convert(
    file=open("sample.mp3", "rb"),
    model_id="scribe_v1",
    timestamps_granularity="character",
)
assert any(w.characters for w in result.words)

# diarization threshold
result = client.speech_to_text.convert(
    file=open("multi-speaker.mp3", "rb"),
    model_id="scribe_v1",
    diarize=True,
    diarization_threshold=0.5,
)
assert result.text
```

### Phase 3 Acceptance

```python
# Webhook push delivery (integration test with ngrok or equivalent)
# POST with webhook=True → job completes → Dalston POSTs to registered URL
# Verify HMAC signature on received payload

# Single-use token
token_resp = requests.post("http://localhost:8000/v1/speech-to-text/realtime/token",
                           headers={"Authorization": "Bearer dalston-key"})
token = token_resp.json()["token"]

async with websockets.connect(
    f"ws://localhost:8000/v1/speech-to-text/realtime?token={token}"
) as ws:
    msg = json.loads(await ws.recv())
    assert msg["message_type"] == "session_started"

# Second use of same token is rejected
async with websockets.connect(
    f"ws://localhost:8000/v1/speech-to-text/realtime?token={token}"
) as ws:
    msg = json.loads(await ws.recv())
    assert msg["message_type"] == "auth_error"
```

---

## Checkpoint

### Phase 1

- [ ] DELETE endpoint implemented and returns 200 `{}`
- [ ] GET in-progress returns 404 + Retry-After
- [ ] `commit_strategy` defaults to `"manual"`
- [ ] `request_id` populated in async response
- [ ] Keyterm word-count limit enforced (≤5 words per term)
- [ ] `enable_logging` accepted without 422
- [ ] Unsupported `audio_format` rejected at handshake with `input_error`
- [ ] `session_started` echoes all accepted parameters
- [ ] `include_language_detection` wired
- [ ] Error types mapped to ElevenLabs vocabulary
- [ ] Spacing tokens synthesised from timestamp gaps
- [ ] `additional_formats` returned inline via ExportService

### Phase 2

- [ ] `logprob` flows from engine → transcript.json → API response (batch + realtime)
- [ ] Character-level timestamps available when engine supports it; `422` otherwise
- [ ] `diarization_threshold` forwarded to pyannote
- [ ] `temperature` and `seed` forwarded to transcribe engine
- [ ] VAD tuning params forwarded to realtime engine
- [ ] `previous_text` forwarded as initial prompt
- [ ] `ulaw_8000` decoded to PCM before forwarding
- [ ] `model_id` routed via config map or rejected with 422

### Phase 3

- [ ] 3 GB / 2 GB file size limits enforced at ingestion
- [ ] `x-ratelimit-*` headers on all batch responses
- [ ] `Idempotency-Key` deduplicates POSTs within 24 hours
- [ ] `POST /v1/speech-to-text/realtime/token` issues single-use tokens
- [ ] WebSocket accepts `?token=` and invalidates after first use
- [ ] Webhook endpoint registry CRUD
- [ ] Delivery worker with retry policy
- [ ] `webhook_id` + `webhook_metadata` passthrough
- [ ] Multi-channel audio fan-out pipeline
- [ ] Entity detection annotations in batch response

---

## What We Are Not Closing

| Gap | Reason |
|---|---|
| `audio_event` word type (laughter, music) | Requires specialist event-detection model; out of scope |
| Per-chunk `sample_rate` mid-stream changes | Not worth implementing; accept and ignore |
| `enable_logging` zero-retention semantics | Self-hosted — retention is operator-controlled |
| ElevenLabs proprietary model behaviour parity | `scribe_v1/v2` are closed-source; we map to comparable open engines |

**Previous milestone**: [M61 OpenAI API Parity](M61-openai-api-parity.md)
**Next milestone**: TBD
