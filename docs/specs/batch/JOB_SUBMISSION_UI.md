# Job Submission Through Web Console (UI/UX Spec)

## Status

Draft

## Owner

Web Console

## Summary

Add job creation to the web console via a dedicated page at `/jobs/new` with file upload or audio URL input, basic defaults for common use, and advanced controls for model/retention/PII options.

This spec chooses a dedicated page instead of a modal because submission has too many fields and states (upload progress, validation, conditional options) for a reliable modal UX, especially on mobile.

---

## 1. Goals

1. Enable admins to submit transcription jobs from the console without using curl/CLI.
2. Keep the primary path fast: upload file + submit in under 20 seconds.
3. Expose advanced API capabilities without overwhelming default users.
4. Provide clear validation and actionable error messages.
5. Redirect directly into existing job monitoring flow after submit.

## 2. Non-Goals

1. Multi-file batch upload in one request.
2. Drag-and-drop folder ingestion.
3. Client-side waveform preview/editing.
4. Persisted user presets (future enhancement).
5. Per-job webhook URL UI (deprecated and usually disabled server-side).

---

## 3. Personas and Primary Jobs

1. Operator: submits a file quickly and monitors pipeline status.
2. Power user: needs explicit model/speaker/PII/retention options.
3. Support engineer: reproduces edge cases from UI without command line.

---

## 4. UX Principles

1. Progressive disclosure: basic fields always visible, advanced fields collapsed.
2. One obvious action: primary CTA is always `Submit Job`.
3. Fail early when possible: client validation for obvious mistakes, server detail surfaced for deep issues.
4. Keep context: success should open the new job detail page immediately.
5. Mobile parity: same capabilities on small screens without horizontal overflow.

---

## 5. Information Architecture and Navigation

### 5.1 Entry Points

1. `Batch Jobs` page header primary button: `Submit Job`.
2. `Batch Jobs` empty state secondary CTA: `Submit your first job`.

Both navigate to `/jobs/new`.

### 5.2 Route

1. Add route: `/jobs/new`.
2. Route title: `Submit Batch Job`.
3. Back navigation: `Back to Jobs` (to `/jobs`).

### 5.3 Post-Submit

1. On `201 Created`, navigate to `/jobs/:jobId`.
2. Show ephemeral success toast: `Job submitted`.
3. Job detail handles polling/status progression (already implemented).

---

## 6. Screen Specification: `/jobs/new`

### 6.1 Layout

Desktop:

1. Two-column layout.
2. Left (main, 2/3): submission form cards.
3. Right (sidebar, 1/3): summary card and guidance card.

Mobile:

1. Single-column stacked cards.
2. Sticky bottom action bar for `Cancel` and `Submit Job`.

### 6.2 Sections (Top to Bottom)

1. Page header:
   - `Submit Batch Job`
   - Subtitle: `Upload audio or provide an audio URL to create a transcription job.`
2. Source card (required):
   - Source type segmented control: `Upload File` | `Audio URL`
3. Basic settings card:
   - Language
   - Speaker detection
   - Timestamp granularity
4. Advanced settings accordion (collapsed by default):
   - Model override
   - Vocabulary
   - Retention policy
   - PII options
5. Submit actions:
   - Secondary: `Cancel`
   - Primary: `Submit Job`

### 6.3 Visual Pattern Requirements

1. Reuse existing console components (`Card`, `Button`, `Select`, badges, inline error text).
2. Keep spacing and typography aligned with existing pages (`BatchJobs`, `Webhooks`, `ApiKeys`).
3. Use muted helper text under non-trivial fields.
4. Do not introduce a new design language for this page.

---

## 7. Form Fields and API Mapping

All requests use `multipart/form-data` to `POST /v1/audio/transcriptions`.

| UI Field | Control | Required | Default | Validation | API Key |
|---|---|---|---|---|---|
| Source type | Segmented control | Yes | `Upload File` | Must select one mode | UI only |
| Audio file | File input + dropzone | Yes when file mode | none | Exactly one file, filename required | `file` |
| Audio URL | URL input | Yes when URL mode | none | Valid URL format | `audio_url` |
| Language | Select + optional custom text | Yes | `auto` | Non-empty string | `language` |
| Speaker detection | Select (`none`,`diarize`,`per_channel`) | Yes | `none` | Allowed enum only | `speaker_detection` |
| Num speakers | Number input | No | empty | 1-32 | `num_speakers` |
| Min speakers | Number input | No | empty | 1-32, <= max if max set | `min_speakers` |
| Max speakers | Number input | No | empty | 1-32, >= min if min set | `max_speakers` |
| Timestamps | Select (`none`,`segment`,`word`) | Yes | `word` | Allowed enum only | `timestamps_granularity` |
| Model | Select (`auto` + available transcribe engines) | Yes | `auto` | Allowed string | `model` |
| Vocabulary | Tag/chips input or multiline text | No | empty | max 100 terms | `vocabulary` (JSON array string) |
| Retention policy | Select from API | No | empty (server default) | Existing policy name | `retention_policy` |
| PII detection | Toggle | No | off | Boolean | `pii_detection` |
| PII tier | Select (`fast`,`standard`,`thorough`) | Yes when PII on | `standard` | Allowed enum | `pii_detection_tier` |
| PII entity types | Tag/chips input | No | empty (= server defaults) | valid tokens | `pii_entity_types` (JSON array string) |
| Redact PII audio | Toggle | No | off | Boolean | `redact_pii_audio` |
| Redaction mode | Select (`silence`,`beep`) | Yes when redact audio on | `silence` | Allowed enum | `pii_redaction_mode` |

### 7.1 Conditional Display Rules

1. `Audio file` is visible only in file mode.
2. `Audio URL` is visible only in URL mode.
3. `PII tier`, `PII entity types`, and `Redact PII audio` appear only when `PII detection` is enabled.
4. `Redaction mode` appears only when `Redact PII audio` is enabled.
5. `Num/Min/Max speakers` are shown only when speaker mode is `diarize` or `per_channel`.

### 7.2 Data Serialization Rules

1. `vocabulary` is serialized to JSON string array.
2. `pii_entity_types` is serialized to JSON string array.
3. Empty optional fields are omitted from payload.
4. Do not send both `file` and `audio_url`.

---

## 8. Validation and Error Handling

### 8.1 Client-Side Validation (Before Submit)

1. Source mode `Upload File`: file is required.
2. Source mode `Audio URL`: URL is required and syntactically valid.
3. Min/max speakers cross-validation when both are set.
4. Vocabulary term count <= 100.
5. PII dependent fields required when toggles are enabled.

### 8.2 Server Error Mapping

Map known backend responses to user-facing text:

| Server Condition | Expected Backend Message | UI Message |
|---|---|---|
| Missing source | `Either 'file' or 'audio_url' must be provided` | `Add an audio file or audio URL.` |
| Both file and URL | `Provide either 'file' or 'audio_url', not both` | `Choose one source type: file or URL.` |
| URL invalid/download issue | `Invalid URL...`, `HTTP 404...`, etc. | Show backend detail inline under Audio URL |
| URL too large | `File too large...` | `The URL file exceeds server size limits.` |
| Invalid audio | `Unable to read audio file...` | Show backend detail under Source card |
| per_channel mono file | `per_channel ... requires stereo audio` | `Per-channel requires stereo audio. Use diarize for mono.` |
| Invalid retention policy | detail object with `param=retention_policy` | `Retention policy not found.` |

### 8.3 Error Presentation

1. Field-level errors directly below the related control.
2. Form-level error banner for unknown or multi-field errors.
3. Optional `Technical details` disclosure containing raw server error text.

---

## 9. Loading, Progress, and Disabled States

1. On submit:
   - Disable all inputs.
   - Primary button shows `Submitting...`.
   - Keep form visible (no route change until success/failure).
2. Upload progress (if supported by client implementation):
   - Show progress bar in Source card.
   - Label: `Uploading audio... {x}%`.
3. Retry behavior:
   - After failure, keep entered values and allow immediate resubmit.

---

## 10. Microcopy

Use exact copy below for first implementation:

1. Source helper (file): `Supported formats include MP3, WAV, FLAC, OGG, and M4A.`
2. Source helper (URL): `Use a direct HTTPS or presigned URL to an audio file.`
3. Advanced accordion label: `Advanced settings`
4. Advanced helper: `Leave fields at defaults unless you need explicit control.`
5. Submit button: `Submit Job`
6. Cancel button: `Cancel`

---

## 11. Accessibility Requirements

1. Every input must have an associated visible label.
2. Required fields announced via semantic attributes.
3. Inline errors connected via `aria-describedby`.
4. Keyboard-only submission path must work end-to-end.
5. Focus order:
   - On load: focus first interactive source control.
   - On error: focus first invalid field.
   - On success redirect: focus job title in Job Detail.

---

## 12. Telemetry and Analytics

Emit client events:

1. `console_job_submit_opened`
2. `console_job_submit_source_selected` with `{source_type}`
3. `console_job_submit_attempted`
4. `console_job_submit_succeeded` with `{job_id, source_type, advanced_used}`
5. `console_job_submit_failed` with `{error_type, source_type}`

Purpose:

1. Measure adoption of UI submission.
2. Track where failures happen.
3. Understand advanced-setting usage before building presets.

---

## 13. Implementation Scope (Frontend)

### 13.1 New/Changed Files

1. `web/src/pages/NewJob.tsx` (new): main page UI and form logic.
2. `web/src/App.tsx`: add `/jobs/new` route.
3. `web/src/pages/BatchJobs.tsx`: add `Submit Job` CTA.
4. `web/src/api/client.ts`: add `createJob(formData)` method using multipart.
5. `web/src/hooks/useCreateJob.ts` (new): mutation + query invalidation.
6. `web/src/api/types.ts`: add request/response types for created job if needed.

### 13.2 Query Invalidation

On success, invalidate:

1. `['jobs']`
2. `['dashboard']` (optional but recommended)

---

## 14. QA Acceptance Criteria

1. User can submit via file upload and is redirected to `/jobs/:id`.
2. User can submit via audio URL and is redirected to `/jobs/:id`.
3. Submitting with empty source shows inline validation.
4. Server error text for URL failures appears in Source card.
5. Advanced settings remain collapsed by default.
6. Mobile layout is fully usable without horizontal scroll.
7. Keyboard-only flow can complete submission.
8. Existing job list/delete/cancel behaviors remain unchanged.

---

## 15. Rollout Plan

1. Phase 1 (MVP):
   - File upload + URL source
   - Basic settings
   - Success redirect
2. Phase 2:
   - Full advanced settings
   - Telemetry
   - Improved error taxonomy
3. Phase 3:
   - Presets / saved defaults
   - Multi-file queue

---

## 16. Open Questions

1. Should model options include only healthy transcribe engines or all registered engines?
2. Should we enforce client-side max file size or rely entirely on backend limits?
3. Do we want a post-submit option to `Submit another` without leaving `/jobs/new`?
