# M26: PII Detection & Audio Redaction

|               |                                                                                           |
| ------------- | ----------------------------------------------------------------------------------------- |
| **Goal**      | Automatically detect PII in transcripts and redact from both text and source audio       |
| **Duration**  | 8-10 days                                                                                 |
| **Dependencies** | M3 (Word Timestamps), M4 (Speaker Diarization), M25 (Data Retention)                   |
| **Deliverable** | PII detection engine, audio redaction, dual output, configurable entity types          |
| **Status**    | Completed                                                                                 |

> **Post-Implementation Update (February 2026):** The detection tier architecture (fast/standard/thorough) described in this milestone was removed after implementation. PII detection now uses GLiNER as the primary NER detector with Presidio restricted to checksum-validated patterns only. See [ADR-009 Amendment](../../decisions/ADR-009-pii-detection-architecture.md#amendment-to-decision-2) for details.

## User Story

> *"As a compliance officer at a regulated organization, I want to automatically detect and redact PII from call recordings so that sensitive data never persists beyond pipeline processing, keeping our infrastructure out of PCI/HIPAA audit scope."*

> *"As an API user, I want both redacted and unredacted transcripts available in a single API response, with entity positions marked, so downstream systems can choose the appropriate version based on authorization level."*

---

## Overview

PII detection and audio redaction are integrated as optional pipeline stages: `TRANSCRIBE → ALIGN → DIARIZE → PII DETECT → AUDIO REDACT → MERGE`. Entity detection produces redacted text, and FFmpeg replaces PII audio spans with silence or beep tones.

---

## Design Decisions

### Pipeline Stage Order

PII detection runs **after** alignment and diarization. See [ADR-009](../../decisions/ADR-009-pii-detection-architecture.md) for full rationale.

**Why this order:**

- **Requires word timestamps:** Audio redaction needs precise word-level timestamps to know exactly when each PII entity was spoken. Alignment provides these.
- **Benefits from diarization:** Knowing which speaker disclosed PII is valuable for compliance. Was it the customer (expected) or the agent (policy violation)?
- **Audio redaction is last:** It's a destructive operation producing a new audio file. All analytical stages must complete first.

### Redact and Delete (Default Pattern)

The pipeline produces redacted text and redacted audio, then **deletes the original unredacted content**. This minimizes compliance scope by eliminating raw PII from persistent storage.

**Compliance properties:**

- **PCI DSS:** No cardholder data at rest. System out of scope for PCI audit on stored data.
- **HIPAA:** Satisfies Safe Harbor de-identification. No PHI persists beyond pipeline execution.
- **GDPR:** Data minimization and storage limitation principles directly addressed.

---

## Steps

### 26.1: Database Schema

**Deliverables:**

- Add PII-related columns to `jobs` table
- Create `pii_entity_types` reference table
- Add indexes for PII filtering queries

PII columns were added to the `jobs` table (enabled flag, tier, entity types, redaction mode, detected count, redacted audio URI) and a `pii_entity_types` reference table was created. See migrations in `alembic/versions/`.

---

### 26.2: Common Types & Enums

**Deliverables:**

- Add `PIIDetectionTier` enum (`fast`, `standard`, `thorough`)
- Add `PIIRedactionMode` enum (`silence`, `beep`)
- Add `PIIEntityCategory` enum (`pii`, `pci`, `phi`)
- Add `PIIEntity` and `PIIAnnotation` models
- Add types to SDK and web console

Enums (`PIIDetectionTier`, `PIIRedactionMode`, `PIIEntityCategory`) and dataclasses (`PIIEntity`, `PIIDetectionResult`) were added to `dalston/common/models.py`. Entity tracks type, category, character offsets, audio timestamps, confidence, speaker, and redacted value.

---

### 26.3: PII Detection Engine Container

**Deliverables:**

- Create `engines/detect/pii-presidio/` directory structure
- Implement Presidio-based detection with GLiNER backend
- Add Luhn checksum validation for credit cards
- Add IBAN mod-97 validation
- Support all entity types from PRD

Implemented as `engines/detect/pii-presidio/`. The engine uses Presidio with custom recognizers (credit card with Luhn, IBAN with mod-97, SSN, phone, regional IDs) and GLiNER (`urchade/gliner_multi-v2.1`) as an NER backend for name/org/location detection. Configurable detection tier, entity type filtering, and confidence threshold. GPU optional (accelerates GLiNER), 4G memory.

---

### 26.4: Audio Redaction Engine

**Deliverables:**

- Create `engines/redact/audio-redactor/` directory structure
- Implement FFmpeg-based audio redaction
- Support silence and beep modes
- Configurable buffer padding around entities

Implemented as `engines/redact/audio-redactor/`. Uses FFmpeg to replace PII audio spans with silence (volume=0) or a 1kHz beep tone in a single pass. Overlapping entity ranges are merged, and configurable buffer padding (default 50ms) is applied around each span. No GPU required, 2G memory.

---

### 26.5: Update DAG Builder

**Deliverables:**

- Add PII detection task creation based on `pii_detection` parameter
- Add audio redaction task creation based on `redact_pii_audio` parameter
- PII detection depends on alignment (for word timestamps)
- Audio redaction depends on PII detection
- Both tasks are optional (don't fail job if not requested)

The DAG builder in `dalston/orchestrator/dag_builder.py` conditionally adds `pii_detect` (after align/diarize) and `audio_redact` (after pii_detect) tasks when the job has PII detection enabled. Both feed into the final merge stage.

---

### 26.6: API Surface

**Deliverables:**

- Add PII parameters to `POST /v1/audio/transcriptions`
- Add PII parameters to `POST /v1/speech-to-text` (ElevenLabs compat)
- Add PII info to job response
- Add entity types list endpoint

**New request parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `pii_detection` | boolean | false | Enable PII detection |
| `pii_detection_tier` | enum | standard | fast/standard/thorough |
| `pii_entity_types` | string[] | null | Entity types to detect (null = defaults) |
| `redact_pii` | boolean | false | Generate redacted transcript |
| `redact_pii_audio` | boolean | false | Generate redacted audio file |
| `pii_redaction_mode` | enum | silence | silence/beep |

**Example request:**

```bash
curl -X POST http://localhost:8000/v1/audio/transcriptions \
  -H "Authorization: Bearer dk_xxx" \
  -F "file=@call.mp3" \
  -F "pii_detection=true" \
  -F "redact_pii=true" \
  -F "redact_pii_audio=true"
```

The job response includes a `pii` summary (tier, entity counts, redacted audio availability), the redacted transcript text, and a full `entities` array with type, category, offsets, timestamps, confidence, speaker, and redacted value for each detected entity.

**New endpoints:** `GET /v1/pii/entity-types`, `GET /v1/audio/transcriptions/{id}/audio/redacted`, `GET /v1/audio/transcriptions/{id}/transcript/unredacted`. See `dalston/gateway/api/v1/pii.py` and `dalston/gateway/api/v1/transcription.py`.

---

### 26.7: Entity Type Registry

**Deliverables:**

- Seed database with default entity types
- Create `GET /v1/pii/entity-types` endpoint
- Support category filtering

The default entity types span PII (name, email, phone, SSN, location, etc.), PCI (credit card, CVV, expiry, IBAN), PHI (medical record, condition, medication), and regional (JMBG, OIB) categories. See the seed data in the database migration and `dalston/common/models.py` for the full list.

---

### 26.8: Merger Integration

**Deliverables:**

- Update final-merger to include PII entities in output
- Include both redacted and unredacted text
- Mark entity positions in segments
- Track redacted audio file reference

The merged output includes both `text` and `redacted_text` at the top level and per-segment, with word-level PII annotations and a `pii_metadata` block containing detection tier, entity count, and redacted audio URI. See `engines/merge/final-merger/engine.py`.

---

### 26.9: Retention Integration

**Deliverables:**

- Integrate with M25 retention system
- Default: delete unredacted content after pipeline completes
- Support `zero-retention` for immediate deletion
- Audit logging for PII detection events

**Retention behavior:**

| Retention Policy | Unredacted Transcript | Redacted Transcript | Redacted Audio | Raw Audio |
|------------------|----------------------|---------------------|----------------|-----------|
| `default` (24h) | Deleted immediately | Kept 24h | Kept 24h | Deleted immediately |
| `zero-retention` | Never stored | Deleted immediately | Deleted immediately | Never stored |
| `keep` | Available via API* | Kept indefinitely | Kept indefinitely | Deleted immediately |

*Unredacted transcript available only if `retain_unredacted=true` parameter is set (Pattern B use case).

---

### 26.10: SDK & CLI Integration

**Deliverables:**

**SDK (`sdk/dalston_sdk/`):**

- Add `pii_detection` parameter to `transcribe()` and `transcribe_async()`
- Add `PIIEntity`, `PIIDetectionResult` types
- Add `list_entity_types()` method

PII parameters (`pii_detection`, `pii_detection_tier`, `redact_pii`, `redact_pii_audio`) were added to `transcribe()` and `transcribe_async()`, along with `PIIEntity`/`PIIDetectionResult` types and a `list_entity_types()` method. See `sdk/dalston_sdk/client.py` and `sdk/dalston_sdk/types.py`.

**CLI (`cli/dalston_cli/`):**

- Add `--pii-detection` flag to `transcribe` command
- Add `--pii-tier` flag (fast/standard/thorough)
- Add `--redact-audio` flag
- Add `dalston pii entity-types` command

See `cli/dalston_cli/commands/transcribe.py` and `cli/dalston_cli/commands/pii.py`.

---

### 26.11: Console Integration

**Deliverables:**

- Add PII toggle to job submission form
- Add entity type selector (grouped by category)
- Display detected entities in job detail page
- Highlight PII in transcript viewer
- Add redacted audio player

**UI components:**

- `PIIEntityBadge` - Colored badge showing entity type and category
- `PIIEntityList` - Filterable list of detected entities
- `RedactedTranscriptViewer` - Toggle between redacted/unredacted views
- `RedactedAudioPlayer` - Audio player with redaction markers on timeline

---

### 26.12: Tests

**Deliverables:**

**Unit tests:**

- Credit card detection with Luhn validation
- IBAN detection with mod-97 validation
- SSN detection with contextual validation
- Phone number detection (international formats)
- GLiNER name/org/location detection
- Audio redaction filter chain generation
- Entity merging and overlap handling

**Integration tests:**

- Submit job with `pii_detection=true`, verify entities detected
- Submit job with `redact_pii_audio=true`, verify redacted audio produced
- Test all three detection tiers (fast/standard/thorough)
- Test entity type filtering
- Test retention integration (verify unredacted content deleted)
- Test ElevenLabs-compatible endpoint with PII params
- End-to-end: audio with known PII → detect → redact → verify silence

**Test fixtures:**

- Sample audio files with scripted PII (cards, names, SSNs)
- Expected entity positions for validation
- Known-good redacted audio for comparison

---

## Verification

- Submit a job with `pii_detection=true` and `redact_pii_audio=true`; confirm entities appear in the response
- Download redacted audio and verify PII spans are silenced/beeped
- Confirm `GET /v1/pii/entity-types` returns categorized entity list
- Verify unredacted content is deleted after pipeline completion (default retention)
- Run `make test` and confirm all PII-related tests pass

---

## Checkpoint

- [ ] `pii_entity_types` table created and seeded with default entities
- [ ] Jobs table has PII-related columns with proper indexes
- [ ] PII detection engine container built and passing health checks
- [ ] Presidio recognizers implemented for all regex-based entities
- [ ] Credit card detection validates via Luhn algorithm
- [ ] IBAN detection validates via mod-97 checksum
- [ ] GLiNER backend integrated for name/org/location/medical entities
- [ ] All three detection tiers working (fast/standard/thorough)
- [ ] Audio redaction engine container built and working
- [ ] Silence mode produces correct audio output
- [ ] Beep mode produces correct audio output
- [ ] Buffer padding configurable and working
- [ ] DAG builder creates PII tasks when requested
- [ ] PII detection runs after alignment stage
- [ ] Audio redaction runs after PII detection
- [ ] API accepts all PII parameters
- [ ] Job response includes entity list and redacted text
- [ ] Redacted audio downloadable via API
- [ ] Entity types endpoint returns categorized list
- [ ] Merger includes PII data in final output
- [ ] Retention system deletes unredacted content by default
- [ ] Audit events emitted for PII detection
- [ ] ElevenLabs endpoint accepts PII parameters
- [ ] SDK has PII parameters and types
- [ ] CLI has PII flags
- [ ] Console shows PII configuration and results
- [ ] All tests passing

---

## Phase 2: Real-Time PII Detection (Future)

After batch PII detection is stable, extend to real-time:

1. Pattern-based PII detection in WebSocket buffer window
2. PII masking in emitted transcript text
3. PII event WebSocket messages for compliance dashboards
4. Stream-to-storage redaction (buffered audio write)

**Estimated duration:** 5-7 days (after M6 Real-Time MVP complete)

---

## Phase 3: Advanced Features (Future)

1. LLM-based contextual PII detection (Thorough tier full implementation)
2. Custom Presidio recognizer API (user-defined patterns)
3. PII detection analytics and reporting dashboard
4. Compliance-specific presets (PCI-DSS, HIPAA Safe Harbor, GDPR)
5. Configurable PII retention and auto-deletion policies
6. PII Vault with role-based access and reconstruct-on-read
7. Pluggable third-party engine support via DIR contract

---

## Unblocked

This milestone enables:

- **PCI DSS compliance**: Credit card data never persists in transcripts
- **HIPAA compliance**: PHI automatically redacted, Safe Harbor de-identification
- **GDPR compliance**: Data minimization through automatic PII removal
- **Regulated industry adoption**: Banking, healthcare, insurance, legal verticals
- **Competitive differentiation**: Only self-hosted solution with text + audio redaction
