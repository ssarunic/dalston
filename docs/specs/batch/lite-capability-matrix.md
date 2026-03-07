# Lite Capability Matrix

> **Single source of truth**: `dalston/orchestrator/lite_capabilities.py`
>
> All content in this document is derived from that module.  If a discrepancy
> arises between this file and the code, the code wins.  Re-generate by calling
> `GET /v1/lite/capabilities` against a running gateway.

## Overview

Lite mode (`DALSTON_MODE=lite`) provides a subset of the full distributed
pipeline through three named profiles.  The active profile is selected by:

1. Explicit `--profile` CLI flag / `lite_profile` API form field
2. `DALSTON_LITE_PROFILE` environment variable
3. Default: `core`

## Profiles

### `core` *(default)*

**Pipeline**: `prepare → transcribe → merge`

**Use when**: You want zero-config transcription with no external dependencies.
This is the M56/M57 baseline path — backward-compatible and unchanged.

| Option | Supported |
|--------|-----------|
| `language` | ✅ |
| `timestamps_granularity` | ✅ |
| `speaker_detection` | ❌ |
| `pii_detection` | ❌ |
| `redact_pii_audio` | ❌ |

**Prerequisites**: None

---

### `speaker`

**Pipeline**: `prepare → transcribe → diarize → merge`

**Use when**: You need speaker-attributed output (who said what).

| Option | Supported |
|--------|-----------|
| `language` | ✅ |
| `timestamps_granularity` | ✅ |
| `speaker_detection=diarize` | ✅ |
| `num_speakers` | ✅ |
| `min_speakers` | ✅ |
| `max_speakers` | ✅ |
| `speaker_detection=per_channel` | ❌ |
| `pii_detection` | ❌ |
| `redact_pii_audio` | ❌ |

**Prerequisites**: None

**Example**:

```bash
dalston transcribe meeting.mp3 --profile speaker --speakers diarize
```

```http
POST /v1/audio/transcriptions
Content-Type: multipart/form-data

file=@meeting.mp3
speaker_detection=diarize
lite_profile=speaker
```

---

### `compliance`

**Pipeline**: `prepare → transcribe → pii_detect → merge`

**Use when**: You need PII detection and optional audio redaction.

> ⚠️ **Conditional**: Requires `presidio_analyzer` and `presidio_anonymizer`
> to be installed.  On a fresh lite install these are absent and this profile
> will raise `LitePrerequisiteMissingError` with installation instructions.

| Option | Supported |
|--------|-----------|
| `language` | ✅ |
| `timestamps_granularity` | ✅ |
| `pii_detection` | ✅ |
| `pii_entity_types` | ✅ |
| `redact_pii_audio` | ✅ |
| `pii_redaction_mode` | ✅ |
| `speaker_detection` | ❌ |

**Prerequisites**: `presidio_analyzer`, `presidio_anonymizer`

```bash
pip install presidio_analyzer presidio_anonymizer
dalston transcribe call.mp3 --profile compliance --pii
```

---

## Unsupported Feature Errors

When a feature is not available in the active profile, the server returns HTTP
`422` with a machine-readable body:

```json
{
  "error": "lite_unsupported_feature",
  "feature": "speaker_detection",
  "profile": "core",
  "remediation": "Use --profile speaker to enable speaker detection in lite mode, or switch to distributed mode for full diarisation support.",
  "upgrade_profiles": ["speaker"]
}
```

The `remediation` field always tells the user exactly what to do next.

## Capability Discovery

```http
GET /v1/lite/capabilities
```

Returns the full matrix as JSON, derived from `lite_capabilities.py`.  No auth
required.  Works in both lite and distributed runtime modes.

Key fields:

| Field | Description |
|-------|-------------|
| `schema_version` | Capability schema version (currently `1.0.0`) |
| `default_profile` | Default profile name (`core`) |
| `active_profile` | Currently active profile (env or default) |
| `profiles` | Map of profile name → capability spec |
| `missing_prereqs` | Per-profile list of absent prerequisite packages |

## What Is Not Supported in Lite Mode

Regardless of profile, lite mode does not support:

- `speaker_detection=per_channel` (requires stereo-split distributed pipeline)
- GPU-dependent model loading (no container orchestration)
- Multi-tenant isolation or API key management beyond basic auth
- Real-time (WebSocket) streaming diarisation or PII

These features require distributed mode.

## Schema Version History

| Version | Change |
|---------|--------|
| `1.0.0` | Initial matrix — `core`, `speaker`, `compliance` profiles (M58) |
