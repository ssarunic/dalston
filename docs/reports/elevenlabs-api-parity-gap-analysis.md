# ElevenLabs ASR API — Parity Gap Analysis

**Scope:** ElevenLabs Speech-to-Text API as documented at
`/v1/speech-to-text` (convert), `/v1/speech-to-text/transcripts/{id}` (get),
`/v1/speech-to-text/transcripts/{id}` (delete), `/v1/speech-to-text/realtime` (WebSocket)

**Dalston implementation reviewed:**
`dalston/gateway/api/v1/speech_to_text.py`
`dalston/gateway/api/v1/realtime.py`

---

## Feasibility Classification

Each gap is rated:

- **✅ Close now** — implementable with current capabilities, no new infrastructure or engine changes
- **🔧 Close with new capability** — requires new infrastructure, new pipeline stage, or engine feature
- **🚫 Architectural limit** — ElevenLabs-proprietary or structurally incompatible with self-hosted model

Remedies are grouped by implementation difficulty: **Easy**, **Medium**, **Hard**.

---

## Gap Inventory

### G01 — DELETE /v1/speech-to-text/transcripts/{id} is missing

**Severity:** 🔴 Critical
**Endpoint:** `DELETE /v1/speech-to-text/transcripts/{id}`
**Feasibility:** ✅ Close now

The route simply does not exist. Any ElevenLabs client that calls DELETE (e.g. to clean up after async processing) receives 404 or 405. The jobs service already has the concept of job ownership and authorisation; a delete handler would cascade to DB row soft-delete and S3 artifact removal.

**Remedy (Medium):** Add `DELETE /transcripts/{transcription_id}` route. Reuse `get_job_authorized` for ownership check. Soft-delete the DB row (`deleted_at`). Issue an async S3 cleanup task. Return `200 {}` on success, `404` if not found, `403` if not owner.

---

### G02 — Wrong default for `commit_strategy` in WebSocket

**Severity:** 🔴 Critical
**Endpoint:** `WSS /v1/speech-to-text/realtime`
**Feasibility:** ✅ Close now

ElevenLabs defaults `commit_strategy` to `"manual"`. Dalston defaults to `"vad"` ([realtime.py:536](../gateway/api/v1/realtime.py)). Any ElevenLabs client that does not explicitly pass this parameter will get automatic VAD-driven commits instead of waiting for its own `commit: true` signals. This will cause spurious early commits mid-utterance for manual-commit clients.

**Remedy (Easy):** Change the default value of the `commit_strategy` query parameter from `"vad"` to `"manual"` at line 536.

---

### G03 — `webhook=true` acknowledges but never delivers

**Severity:** 🔴 Critical
**Endpoint:** `POST /v1/speech-to-text`
**Feasibility:** 🔧 Close with new capability

ElevenLabs async mode POSTs results to a configured webhook URL. Dalston returns the `transcription_id` immediately but never pushes results anywhere — the client must poll `GET /transcripts/{id}`. This is a semantic mismatch: push vs. pull. Any integration that submits large files with `webhook=true` and waits for the callback will time out or never receive results.

**Remedy (Hard):** Build an outbound webhook delivery subsystem:

- Webhook endpoint registration (URL + secret per tenant)
- `webhook_id` routing to a specific registered endpoint
- `webhook_metadata` passthrough in the payload
- Signed delivery (HMAC), retry with exponential backoff
- The `request_id` field should be populated and echoed in the webhook body

Until implemented, the response should include a `warning` or documented note that push delivery is not supported.

---

### G04 — GET /transcripts/{id} returns non-spec "processing" response

**Severity:** 🟠 High
**Endpoint:** `GET /v1/speech-to-text/transcripts/{id}`
**Feasibility:** ✅ Close now

ElevenLabs only defines two outcomes for GET: a completed transcript (200) or not-found (404). There is no in-progress variant. Dalston returns a custom `ElevenLabsProcessingResponse` (`{"status": "processing", ...}`) for jobs that are still running. Strict ElevenLabs clients will fail to parse this or treat it as a malformed response.

The ElevenLabs async pattern is: submit → get `transcription_id` → poll GET until the webhook fires or the client decides to call GET. The implication is GET should return 404 (or keep returning 404) until the transcript is ready, not a custom object.

**Remedy (Easy):** For in-progress jobs, return `404` with a body of `{"detail": "Transcription not ready"}` and a `Retry-After` header. For failed/cancelled jobs, return an appropriate error code (e.g. `500` or `410 Gone`). Remove `ElevenLabsProcessingResponse` or move it to the native Dalston API only.

---

### G05 — `token` query param not supported for WebSocket auth

**Severity:** 🟠 High
**Endpoint:** `WSS /v1/speech-to-text/realtime`
**Feasibility:** 🔧 Close with new capability

ElevenLabs supports single-use token auth via `?token=<token>` at connection time. This is the standard pattern for browser-based clients that cannot embed API keys safely. Dalston uses `?api_key=<key>` (different name) and does not support single-use tokens. A browser client using the ElevenLabs SDK will silently fail auth.

**Remedy (Medium):** Add a REST endpoint to generate a short-lived, single-use WebSocket token tied to a tenant. In the WebSocket handler, also accept `?token=` in addition to `?api_key=`. Invalidate the token after first use.

---

### G06 — `words[].type` always `"word"` — spacing and audio_event tokens absent

**Severity:** 🟠 High
**Endpoint:** `POST /v1/speech-to-text`, `GET /transcripts/{id}`, `WSS realtime`
**Feasibility:**

- `spacing` tokens: ✅ Close now (synthetic)
- `audio_event` tokens: 🔧 Close with new capability

ElevenLabs emits three word types: `word`, `spacing` (silence/pause between words), and `audio_event` (non-speech sounds tagged by `tag_audio_events`). Dalston hardcodes `"word"` everywhere. The `spacing` type represents pauses and is important for subtitle renderers and transcription editors.

**Remedy — spacing (Easy):** Synthesise `spacing` tokens by inspecting the gap between adjacent word `end` and `start` timestamps. Insert a `{ "type": "spacing", "text": " ", "start": prev.end, "end": next.start }` token wherever the gap exceeds a threshold (e.g. 0.1s). This requires no engine changes.

**Remedy — audio_event (Hard):** Requires a dedicated audio event detection model (e.g. for laughter, applause, music). Not feasible without a new engine stage.

---

### G07 — `words[].logprob` never emitted

**Severity:** 🟠 High
**Endpoint:** `POST /v1/speech-to-text`, `GET /transcripts/{id}`, `WSS realtime`
**Feasibility:** ✅ Close now (data likely already present)

WhisperX and faster-whisper both produce per-word probability scores. The data exists in engine output but is not threaded through the pipeline into the API response or the transcript stored in S3. `logprob` (log probability, ≤0) is used by downstream tooling for confidence filtering and transcript QA.

**Remedy (Medium):** Verify the merge engine passes `logprob` through to `transcript.json`. Update `ElevenLabsWord`, `_format_elevenlabs_response`, and the realtime `committed_transcript_with_timestamps` translator to emit it. No new capability needed.

---

### G08 — `model_id` silently ignored

**Severity:** 🟡 Medium
**Endpoint:** `POST /v1/speech-to-text`, `WSS realtime`
**Feasibility:** 🔧 Close with new capability

Dalston accepts `model_id` (scribe_v1, scribe_v2) but uses `settings.default_model` unconditionally. ElevenLabs clients may rely on model tier selection for latency vs. accuracy trade-offs. The current behaviour is invisible to the caller.

**Remedy (Medium):** Introduce a configuration mapping from ElevenLabs model IDs to Dalston engine names. If the requested model is not available, return a `422` with a clear message rather than silently substituting. This at minimum makes the gap explicit rather than silent.

---

### G09 — `tag_audio_events` accepted but does nothing

**Severity:** 🟡 Medium
**Endpoint:** `POST /v1/speech-to-text`
**Feasibility:** 🔧 Close with new capability (for real behaviour) / ✅ Close now (for honesty)

The parameter is accepted as a form field but never passed to any engine. Audio event tagging requires a model capable of classifying non-speech events (laughter, applause, music, footsteps).

**Remedy (Easy — partial):** If `tag_audio_events=true` and Dalston cannot deliver events, return a `warning` in the response body or a response header. Do not silently drop the request.

**Remedy (Hard — full):** Implement a dedicated audio event detection stage (e.g. using PANNs or a fine-tuned Whisper). This is a new engine stage.

---

### G10 — `timestamps_granularity: "character"` silently downgraded

**Severity:** 🟡 Medium
**Endpoint:** `POST /v1/speech-to-text`
**Feasibility:** ✅ Close now (data may already be available)

Dalston maps `"character"` to `"word"` silently ([speech_to_text.py:113](../gateway/api/v1/speech_to_text.py)). WhisperX actually supports character-level alignment — the data may be available in the engine but is discarded. If not available, the downgrade should be explicit (either reject with 422 or document in the response).

**Remedy (Medium):** Check whether WhisperX alignment output includes character-level timestamps. If yes, thread `characters: [{text, start, end}]` arrays through to `ElevenLabsWord`. If no, return 422 when `character` granularity is requested, with a message explaining the limitation. Either way, remove the silent mapping.

---

### G11 — `words[].characters` never populated

**Severity:** 🟡 Medium
**Endpoint:** `POST /v1/speech-to-text`, `GET /transcripts/{id}`
**Feasibility:** ✅ Close now (linked to G10)

This is the output side of G10. WhisperX alignment produces character-level timestamps. If the pipeline stores and forwards them, populating `characters` in the response requires only model and formatter changes. Depends on G10 being resolved.

---

### G12 — Missing WebSocket VAD tuning parameters

**Severity:** 🟡 Medium
**Endpoint:** `WSS /v1/speech-to-text/realtime`
**Feasibility:** 🔧 Close with new capability

ElevenLabs exposes `vad_silence_threshold_secs`, `vad_threshold`, `min_speech_duration_ms`, `min_silence_duration_ms` as connection-time tuning knobs. Dalston accepts none of these. Applications fine-tuning for phone audio, noisy environments, or short utterances cannot get equivalent behaviour.

**Remedy (Medium):** If the underlying realtime engine (e.g. Silero VAD in whisper-streaming) accepts these parameters, pass them through from the WebSocket query string. Requires realtime SDK / engine changes to accept and apply them. If the engine does not support them, at minimum accept the parameters without error (and echo them in `session_started` as a signal to the client).

---

### G13 — `session_started` config echo is incomplete

**Severity:** 🟡 Medium
**Endpoint:** `WSS /v1/speech-to-text/realtime`
**Feasibility:** ✅ Close now

ElevenLabs echoes the full effective config in `session_started` so the client can confirm active settings. Dalston only echoes `{sample_rate, audio_format, language_code, model_id, commit_strategy}`. Missing: `vad_silence_threshold_secs`, `vad_threshold`, `min_speech_duration_ms`, `min_silence_duration_ms`, `enable_logging`, `include_timestamps`, `include_language_detection`.

**Remedy (Easy):** Add all accepted (and where relevant, defaulted) parameter values to the `session_started.config` payload, even for params that are not yet acted on. This costs nothing and closes the observable gap from the client's perspective.

---

### G14 — `include_language_detection` not supported in WebSocket

**Severity:** 🟡 Medium
**Endpoint:** `WSS /v1/speech-to-text/realtime`
**Feasibility:** ✅ Close now (language already detected)

Dalston performs language detection during realtime transcription (it is passed to the worker). The flag `include_language_detection` just controls whether the detected language appears in the `committed_transcript_with_timestamps` payload. This is purely a response-formatting feature.

**Remedy (Easy):** Accept `include_language_detection` as a query param. When `true`, include `language_code` in `committed_transcript` messages (not just `committed_transcript_with_timestamps`). The worker already provides this data.

---

### G15 — Error message types collapsed to generic `"error"`

**Severity:** 🟡 Medium
**Endpoint:** `WSS /v1/speech-to-text/realtime`
**Feasibility:** ✅ Close now (partial), 🔧 for full coverage

ElevenLabs defines 13 structured error `message_type` values (`auth_error`, `quota_exceeded`, `rate_limited`, `queue_overflow`, `resource_exhausted`, `session_time_limit_exceeded`, `chunk_size_exceeded`, etc.). Dalston emits a single generic `"error"` type for all failures. Clients that branch on error type for retry logic or user-facing messaging cannot distinguish conditions.

**Remedy (Medium):** Map Dalston's internal error conditions to the appropriate ElevenLabs error types:

- Auth failure → `auth_error`
- Rate limit → `rate_limited`
- No capacity → `queue_overflow`
- Audio chunk too large → `chunk_size_exceeded`
- Session duration → `session_time_limit_exceeded`
- Internal transcription failure → `transcriber_error`

This requires tagging errors at the source with a machine-readable code.

---

### G16 — `diarization_threshold` not supported

**Severity:** 🟡 Medium
**Endpoint:** `POST /v1/speech-to-text`
**Feasibility:** 🔧 Close with new capability

ElevenLabs exposes `diarization_threshold` (sensitivity for speaker separation, ~0.22 default) when `diarize=true` and `num_speakers` is unset. pyannote (used in Dalston's diarize engine) has an equivalent threshold parameter. Whether it is exposed as a job parameter is an implementation question.

**Remedy (Medium):** Accept `diarization_threshold` as a form field. Pass it through to the diarize task parameters. The diarize engine needs to read and apply it. Validate range (0.0–1.0).

---

### G17 — `additional_formats` not returned inline

**Severity:** 🟡 Medium
**Endpoint:** `POST /v1/speech-to-text`, `GET /transcripts/{id}`
**Feasibility:** ✅ Close now

ElevenLabs returns export formats (SRT, TXT, DOCX, HTML, PDF, segmented JSON) inline in the transcription response under `additional_formats`. Dalston has a separate `GET /transcripts/{id}/export/{format}` endpoint (which is not in the ElevenLabs spec). Clients that expect inline formats will receive nothing.

**Remedy (Medium):** Accept the `additional_formats` form field (currently ignored). After transcript assembly in `_format_elevenlabs_response`, call the existing `ExportService` for each requested format and embed the result as a base64-encoded string in the response. The export service already exists; this is plumbing work.

Note: The separate export endpoint Dalston provides is a useful extension but is invisible to ElevenLabs-compatible clients.

---

### G18 — `use_multi_channel` and multichannel response not supported

**Severity:** 🟡 Medium
**Endpoint:** `POST /v1/speech-to-text`
**Feasibility:** 🔧 Close with new capability

Multi-channel transcription requires splitting audio by channel, running independent transcription tasks for each, and assembling a `MultichannelSpeechToTextResponseModel` with per-channel results and `channel_index` on each word. This is a significant pipeline extension.

**Remedy (Hard):** Add an `audio-split` pre-processing stage that extracts channels, fans out to parallel `TRANSCRIBE` tasks, and merges results. The merge engine would need to assemble the multichannel response shape. Not feasible without architectural pipeline work.

---

### G19 — `entity_detection` not supported

**Severity:** 🟡 Medium
**Endpoint:** `POST /v1/speech-to-text`
**Feasibility:** 🔧 Close with new capability

ElevenLabs performs PII/PHI/PCI/offensive language entity detection and returns character-position annotations. Dalston has a `PII_DETECT` pipeline stage but it is oriented toward audio redaction, not inline response annotation.

**Remedy (Hard):** Extend the `PII_DETECT` stage to emit entity annotations in the ElevenLabs format (`text`, `entity_type`, `start_char`, `end_char`). Thread these through the merge engine into `transcript.json` and surface them in the API response. The stage needs to support the `entity_detection` parameter for selective detection types.

---

### G20 — `temperature` and `seed` not forwarded

**Severity:** 🟢 Low
**Endpoint:** `POST /v1/speech-to-text`
**Feasibility:** 🔧 Close with new capability

faster-whisper supports `temperature` (and a temperature fallback list) and `repetition_penalty`. Determinism via `seed` may be partially achievable. Both are accepted by ElevenLabs as generation controls.

**Remedy (Medium):** Accept both parameters (form fields). Pass them to the transcribe engine task parameters. The transcribe engine needs to read and apply them. Validate ranges: temperature 0.0–2.0, seed 0–2,147,483,647.

---

### G21 — `keyterms` word-count per term not enforced

**Severity:** 🟢 Low
**Endpoint:** `POST /v1/speech-to-text`, `WSS realtime`
**Feasibility:** ✅ Close now

ElevenLabs rejects keyterms with more than 5 words each. Dalston only validates character length (≤50) and total count (≤100). The word-count limit is trivially enforceable.

**Remedy (Easy):** Add `len(term.split()) > 5 → 400` validation in both the batch endpoint ([speech_to_text.py:242](../gateway/api/v1/speech_to_text.py)) and the WebSocket handler ([realtime.py:619](../gateway/api/v1/realtime.py)).

---

### G22 — `request_id` always `null` in async responses

**Severity:** 🟢 Low
**Endpoint:** `POST /v1/speech-to-text`
**Feasibility:** ✅ Close now

The async response model has `request_id: str | None = None` and is always null. The gateway already extracts `X-Request-ID` via `request.state.request_id`.

**Remedy (Easy):** Populate `request_id=request_id` when constructing `ElevenLabsAsyncResponse` at [speech_to_text.py:307](../gateway/api/v1/speech_to_text.py).

---

### G23 — `previous_text` context hint in first audio chunk ignored

**Severity:** 🟢 Low
**Endpoint:** `WSS /v1/speech-to-text/realtime`
**Feasibility:** 🔧 Close with new capability

ElevenLabs allows the client to provide `previous_text` in the first `input_audio_chunk` as a transcript context hint to improve accuracy at session start. The realtime worker would need to accept and apply an initial prompt.

**Remedy (Medium):** Extract `previous_text` from the first `input_audio_chunk` in `_elevenlabs_client_to_worker`. Forward it as a session parameter when connecting to the worker (e.g. `initial_prompt=...` in the worker URL). Requires realtime SDK / engine support for initial prompts.

---

### G24 — `per-chunk sample_rate` in audio chunks ignored

**Severity:** 🟢 Low
**Endpoint:** `WSS /v1/speech-to-text/realtime`
**Feasibility:** 🚫 Architectural limit (partial)

ElevenLabs allows `sample_rate` per `input_audio_chunk`. In practice this is a signal for mid-stream format changes. Dalston negotiates sample rate once at connection time and cannot change it mid-session. Supporting mid-stream resampling would require the worker protocol to accept per-frame metadata.

**Remedy:** Accept the field without error. If it differs from the session sample rate, log a warning. True mid-stream resampling is not worth implementing.

---

### G25 — `ulaw_8000` audio format not handled

**Severity:** 🟢 Low
**Endpoint:** `WSS /v1/speech-to-text/realtime`
**Feasibility:** 🔧 Close with new capability

ElevenLabs supports `ulaw_8000` (μ-law encoding, common for telephony). Dalston's audio format parser only extracts sample rate from `pcm_XXXX` patterns; `ulaw_8000` falls through to the default sample rate with no decoding. Raw μ-law bytes sent to the worker will produce garbage transcription.

**Remedy (Medium):** Detect `ulaw_8000` format at the gateway. Add a μ-law → PCM decoder in the `_elevenlabs_client_to_worker` handler before forwarding to the worker. This is a well-defined codec conversion requiring a small library (e.g. `audioop` or `soundfile`).

---

### G26 — `enable_logging` / zero-retention mode not supported

**Severity:** 🟢 Low
**Endpoint:** `POST /v1/speech-to-text`, `WSS realtime`
**Feasibility:** 🚫 Architectural limit (for ElevenLabs semantics) / 🔧 Partial

For ElevenLabs this is an enterprise zero-retention flag that prevents server-side logging and storage of the transcript. For a self-hosted system, the operator controls retention by design, so the parameter has no meaningful ElevenLabs-equivalent behaviour.

**Remedy:** Accept and ignore the parameter to avoid 422 validation errors. Document that retention is operator-controlled in self-hosted deployments.

---

### G27 — Non-spec server messages from Dalston

**Severity:** 🟢 Low
**Endpoint:** `WSS /v1/speech-to-text/realtime`
**Feasibility:** ✅ Close now (guard against strict clients)

Dalston emits `speech_started`, `speech_ended`, `session_ended`, and `warning` messages. None of these are in the ElevenLabs realtime spec. Strict ElevenLabs clients that only handle known message types will silently drop them, which is benign. However, a client that errors on unknown types will break.

**Remedy (Easy):** No change required unless a specific client breaks. These are useful extensions. Document them.

---

## Remedies by Difficulty

### Easy — Low effort, no architectural change

| ID | Remedy |
|---|---|
| G02 | Change `commit_strategy` default to `"manual"` |
| G04 | Return 404 + `Retry-After` for in-progress transcripts |
| G06 (partial) | Synthesise `spacing` word tokens from timestamp gaps |
| G13 | Complete `session_started` config echo with all accepted params |
| G14 | Accept and act on `include_language_detection` |
| G21 | Enforce ≤5 words per keyterm |
| G22 | Populate `request_id` from `X-Request-ID` in async response |
| G26 | Accept `enable_logging` without 422 |
| G27 | Document non-spec messages |

### Medium — Meaningful implementation work, no new infrastructure

| ID | Remedy |
|---|---|
| G01 | Implement DELETE /transcripts/{id} |
| G07 | Thread `logprob` from engine output to API response |
| G10 | Proper handling of `timestamps_granularity: "character"` (reject or implement) |
| G11 | Populate `words[].characters` if WhisperX data available |
| G15 | Map internal errors to ElevenLabs error type vocabulary |
| G17 | Inline `additional_formats` in POST response via existing ExportService |
| G20 | Forward `temperature` and `seed` to transcribe engine |
| G23 | Forward `previous_text` to realtime worker as initial prompt |
| G25 | Add μ-law decoder in WebSocket audio path |

### Hard — Requires new infrastructure, new engine stage, or significant new capability

| ID | Remedy |
|---|---|
| G03 | Outbound webhook push delivery system |
| G05 | Single-use token endpoint + WebSocket token auth |
| G08 | Model ID → engine mapping (or explicit rejection) |
| G12 | VAD tuning params in realtime (engine changes required) |
| G16 | `diarization_threshold` forwarded to pyannote |
| G18 | Multi-channel transcription pipeline |
| G19 | Entity detection annotations in response |

### Not worth closing / Architectural limit

| ID | Note |
|---|---|
| G24 | Per-chunk sample rate — not worth implementing |
| G26 | Zero-retention semantics — operator-controlled in self-hosted |
| G06 (audio_event) | Audio event detection requires specialist model |

---

## Blind Spots Not Yet Addressed

These are gaps in the analysis itself — areas not covered by the four audited endpoints but relevant to overall ElevenLabs parity.

### B01 — No WebSocket token issuance endpoint

ElevenLabs has a dedicated REST endpoint to generate short-lived tokens for WebSocket auth (for browser clients). Without it, `?token=` auth (G05) cannot be implemented. More importantly, there is currently no safe way to connect to the realtime endpoint from a browser without embedding the API key.

### B02 — No transcript list endpoint

ElevenLabs likely has (or will add) a `GET /v1/speech-to-text/transcripts` list endpoint. Dalston has a native jobs list endpoint but no ElevenLabs-namespaced equivalent. Any SDK that auto-discovers past transcriptions will find nothing.

### B03 — `transcription_id` format differs from ElevenLabs

ElevenLabs likely uses opaque string IDs (e.g. `sub_01JV...`). Dalston uses UUIDs. While UUID is a valid string, any ElevenLabs client SDK that validates ID format (regex, prefix checks) may reject Dalston IDs. This is unverifiable without access to the ElevenLabs SDK source but worth testing.

### B04 — `cloud_storage_url` provider coverage is unverified

ElevenLabs documents support for presigned S3/GCS URLs, Google Drive, and Dropbox. Dalston's ingestion service accepts HTTPS URLs but the set of providers actually tested is unclear. Dropbox and Google Drive use redirect chains and require specific HTTP client handling (follow redirects, handle auth-gated responses). A presigned S3 URL that expires before ingestion completes will produce an opaque download failure.

### B05 — File size limit not enforced at the gateway

ElevenLabs enforces a 3 GB limit for file uploads and 2 GB for `cloud_storage_url`. Dalston has no visible file size validation in `speech_to_text.py`. Large uploads will be streamed to S3 before any limit is applied, potentially exhausting storage and causing late-stage failures instead of fast client-visible 422 errors.

### B06 — Idempotency key support absent

ElevenLabs is generally safe to retry (the spec notes mutations should be idempotent). If a client retries a timed-out POST (e.g. on a slow upload), Dalston will create a duplicate job. There is no idempotency key mechanism (`Idempotency-Key` header) to prevent duplicate processing.

### B07 — No rate-limiting headers in responses

ElevenLabs returns rate-limiting metadata (`X-RateLimit-*` headers or equivalent). Dalston enforces rate limits internally but does not expose them in response headers. Clients that back off based on rate limit headers will not get the signal.

### B08 — CORS not verified for browser-based WebSocket

The realtime endpoint needs specific CORS / upgrade handling for browser-based clients (e.g. the ElevenLabs JavaScript SDK). The current WebSocket handler may not be configured for cross-origin connections from arbitrary browser origins.

### B09 — `file_format: "pcm_s16le_16"` fast-path not implemented

ElevenLabs allows clients to declare `file_format: "pcm_s16le_16"` to skip format detection for raw PCM. Dalston always probes the file with ffprobe. For high-throughput use cases (e.g. call-centre pipelines submitting pre-encoded PCM), this adds avoidable latency on every request. The parameter is accepted but ignored.

### B10 — Realtime `ulaw_8000` acceptance advertising

The WebSocket handler does not validate `audio_format` against a whitelist of supported values. If a client sends `audio_format=ulaw_8000` (which Dalston does not decode), it will be accepted at connection time and produce garbage transcription output. The session should be rejected at handshake with a clear error if the format is unsupported.

### B11 — Worker stats and usage not surfaced

ElevenLabs responses include character/word counts and duration used for billing. Dalston's `session_ended` message includes `total_audio_seconds` but no word or character count. This matters if Dalston ever exposes usage metering or if clients use these counts for downstream processing.

---

## Recommended Implementation Order

Based on severity and implementation cost, the following order maximises parity for minimum effort:

**Sprint 1 — No-risk fixes (hours each):**

1. G02 — Fix `commit_strategy` default
2. G22 — Populate `request_id`
3. G21 — Enforce keyterm word-count limit
4. G13 — Complete `session_started` echo
5. G14 — Wire `include_language_detection`
6. G26 — Accept `enable_logging` gracefully
7. B10 — Reject unsupported `audio_format` at handshake

**Sprint 2 — High-value medium work (days each):**

1. G01 — Implement DELETE endpoint
2. G04 — Fix GET transcript response for in-progress jobs
3. G15 — Map error types
4. G07 — Thread `logprob` through pipeline
5. G06 (spacing) — Synthesise spacing tokens

**Sprint 3 — Capability extensions (weeks each):**

1. G05 — Single-use token endpoint
2. G03 — Webhook push delivery
3. G17 — Inline `additional_formats`
4. G10/G11 — Character-level timestamps
5. G25 — μ-law decoding

**Backlog (significant scope):**

- G18 — Multi-channel
- G19 — Entity detection
- G12 — VAD tuning params
- G16 — Diarization threshold
