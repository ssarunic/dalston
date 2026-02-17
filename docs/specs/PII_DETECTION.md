# PII Detection & Audio Redaction

## Strategic

### Goal

Automatically identify personally identifiable information (PII) in transcripts and redact it from both text and source audio, enabling regulated-industry customers to process sensitive recordings while maintaining compliance with PCI DSS, HIPAA, and GDPR.

### Scope

This spec covers the PII detection and audio redaction system: entity types, detection engines, redaction modes, API integration, and data retention strategies.

**In scope:**

- PII detection pipeline stage (after alignment and diarization)
- Audio redaction pipeline stage (after PII detection)
- Three detection tiers (fast/standard/thorough)
- Entity type configuration and categories (PII, PCI, PHI)
- Redaction modes (silence, beep)
- Dual output (redacted and unredacted text)
- Integration with retention system (redact-and-delete pattern)
- ElevenLabs API compatibility

**Out of scope:**

- Real-time PII detection (Phase 2, after M6)
- LLM-based contextual detection (Phase 3, thorough tier full implementation)
- Custom recognizer API (Phase 3)
- PII Vault with role-based access (Phase 3, enterprise feature)
- PII analytics dashboard (Phase 3)

**Related documents:**

- [ADR-009: PII Detection Architecture](../decisions/ADR-009-pii-detection-architecture.md) — Architectural decisions
- [M26: PII Detection & Audio Redaction](../plan/milestones/M26-pii-detection-redaction.md) — Implementation plan
- [Data Retention](DATA_RETENTION.md) — Retention policies, cleanup integration
- [Pipeline Interfaces](PIPELINE_INTERFACES.md) — Stage interface specifications
- [Engines](batch/ENGINES.md) — Engine SDK and container patterns

### User Stories

1. As a **compliance officer**, I want PII automatically detected and redacted so call recordings don't create audit scope
2. As an **API user**, I want both redacted and unredacted transcripts so downstream systems can choose based on authorization
3. As a **bank**, I want credit card numbers validated via Luhn before redaction to minimize false positives
4. As a **healthcare org**, I want PHI detection for diagnoses and medications to support HIPAA compliance
5. As an **operator**, I want to configure which entity types are detected to balance accuracy and performance

### Competitive Context

| Provider | Text PII | Audio Redact | Self-Hosted | Multi-Lang |
|----------|----------|--------------|-------------|------------|
| AssemblyAI | Yes | Yes (cloud) | No | Limited |
| ElevenLabs | Yes (56 types) | No | No | Limited |
| AWS Transcribe | Yes | No | No | Yes |
| Deepgram | Yes | No | On-prem opt | Yes |
| **Dalston** | **Yes** | **Yes** | **Yes** | **Yes** |

Dalston is the only self-hosted solution offering both text and audio redaction as part of the transcription pipeline. With cloud providers, unredacted audio still travels to their servers before detection.

---

## Tactical

### Pipeline Integration

PII detection and audio redaction integrate as two new stages in the batch pipeline:

```
PREPARE → TRANSCRIBE → ALIGN → DIARIZE → PII_DETECT → AUDIO_REDACT → MERGE
                                              │              │
                                      Requires word    Requires entity
                                      timestamps       timestamps
```

#### Why This Order

| Dependency | Reason |
|------------|--------|
| PII after ALIGN | Audio redaction requires precise word-level timestamps |
| PII after DIARIZE | Speaker attribution valuable for compliance (who disclosed PII?) |
| AUDIO_REDACT last | Destructive operation; all analysis must complete first |

### Detection Tiers

Three tiers allow balancing speed, accuracy, and resource usage:

| Tier | Engine Stack | Compute | Latency | Coverage |
|------|--------------|---------|---------|----------|
| `fast` | Presidio regex + checksum | CPU | < 5ms | Cards, SSNs, IBANs, emails, phones, IPs |
| `standard` | Presidio + GLiNER | CPU | ~100ms | + Names, orgs, locations, medical |
| `thorough` | Presidio + GLiNER + LLM | GPU/API | 1-3s | + Contextual/indirect PII |

#### Fast Tier

Uses Microsoft Presidio's regex-based recognizers with validation:

- **Credit cards**: Pattern matching + Luhn algorithm validation
- **IBANs**: ISO 13616 pattern + mod-97 checksum validation
- **SSNs**: US format with contextual validation
- **Phone numbers**: International formats (+386, +385, +381, etc.)
- **Email addresses**: RFC 5322 pattern
- **IP addresses**: IPv4 and IPv6

This tier covers 80%+ of regulated PII with zero language dependency.

#### Standard Tier

Adds GLiNER (Generalist Model for Named Entity Recognition) as a Presidio NER backend:

- **Names**: Handles non-Western names (Slavic, Asian, Indian)
- **Organizations**: Company and institution names
- **Locations**: Addresses, cities, countries
- **Medical**: Conditions, medications (for PHI detection)

GLiNER's zero-shot bidirectional transformer architecture provides strong multilingual coverage without language-specific training.

#### Thorough Tier

Adds LLM-based contextual detection for indirect PII:

- "the house on the corner of 5th and Main" → location
- "my mother's maiden name" → identity information
- "the account I opened last Tuesday" → temporal context

Implementation deferred to Phase 3.

### Entity Types

#### PII Category (Personal)

| Entity Type | Detection Method | Validation | Default |
|-------------|------------------|------------|---------|
| `name` | GLiNER | — | Yes |
| `name_given` | GLiNER | — | No |
| `name_family` | GLiNER | — | No |
| `email_address` | Presidio regex | RFC 5322 | Yes |
| `phone_number` | Presidio regex | International | Yes |
| `ssn` | Presidio regex | US format + context | Yes |
| `location` | GLiNER | — | Yes |
| `location_address` | GLiNER | — | No |
| `date_of_birth` | GLiNER + regex | Date patterns | Yes |
| `age` | GLiNER | — | No |
| `ip_address` | Presidio regex | IPv4/IPv6 | Yes |
| `driver_license` | Presidio regex | Country-specific | No |
| `passport_number` | Presidio regex | Country-specific | No |
| `organization` | GLiNER | — | No |

#### PCI Category (Payment Card Industry)

| Entity Type | Detection Method | Validation | Default |
|-------------|------------------|------------|---------|
| `credit_card_number` | Presidio regex | **Luhn algorithm** | Yes |
| `credit_card_cvv` | Regex + context | 3-4 digits near card | Yes |
| `credit_card_expiry` | Regex | MM/YY, MM/YYYY | Yes |
| `iban` | Regex | **ISO 13616 mod-97** | Yes |
| `bank_account` | GLiNER + regex | Country-specific | No |

#### PHI Category (Protected Health Information)

| Entity Type | Detection Method | Validation | Default |
|-------------|------------------|------------|---------|
| `medical_record_number` | Regex | MRN patterns | No |
| `medical_condition` | GLiNER | — | No |
| `medication` | GLiNER | — | No |
| `health_plan_id` | Regex | Insurance IDs | No |

#### Regional (SE European)

| Entity Type | Detection Method | Validation | Default |
|-------------|------------------|------------|---------|
| `jmbg` | Regex | 13-digit Yugoslav/Serbian ID + checksum | No |
| `oib` | Regex | 11-digit Croatian ID + ISO 7064 Mod 11,10 | No |

### Data Model

#### Jobs Table Extensions

```sql
ALTER TABLE jobs ADD COLUMN pii_detection_enabled BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE jobs ADD COLUMN pii_detection_tier VARCHAR(20);
ALTER TABLE jobs ADD COLUMN pii_entity_types TEXT[];
ALTER TABLE jobs ADD COLUMN pii_redact_audio BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE jobs ADD COLUMN pii_redaction_mode VARCHAR(20);
ALTER TABLE jobs ADD COLUMN pii_entities_detected INTEGER;
ALTER TABLE jobs ADD COLUMN pii_redacted_audio_uri TEXT;

COMMENT ON COLUMN jobs.pii_detection_tier IS 'fast, standard, or thorough';
COMMENT ON COLUMN jobs.pii_redaction_mode IS 'silence or beep';
```

#### PIIEntity Model

```python
@dataclass
class PIIEntity:
    """A detected PII entity with position and timing information."""
    entity_type: str                # e.g., "credit_card_number"
    category: str                   # "pii", "pci", or "phi"
    text: str                       # The detected text (available until pipeline cleanup)
    start_offset: int               # Character offset in transcript
    end_offset: int                 # Character offset in transcript
    start_time: float               # Audio timestamp (seconds)
    end_time: float                 # Audio timestamp (seconds)
    confidence: float               # 0.0-1.0
    speaker: str | None             # Speaker ID if diarized
    redacted_value: str             # e.g., "****7890" or "[CREDIT_CARD]"
    detection_method: str           # "regex", "gliner", "llm"
```

#### PIIDetectionResult Model

```python
@dataclass
class PIIDetectionResult:
    """Output from PII detection stage."""
    entities: list[PIIEntity]
    redacted_text: str
    entity_count_by_type: dict[str, int]
    entity_count_by_category: dict[str, int]
    detection_tier: str
    processing_time_ms: int
    warnings: list[str]
```

### Audio Redaction

Audio redaction uses FFmpeg, already present in the preprocessing container.

#### Redaction Modes

| Mode | Implementation | Use Case |
|------|----------------|----------|
| `silence` | FFmpeg `volume=0` filter | Default, unobtrusive |
| `beep` | FFmpeg 1kHz tone overlay | Explicit redaction indicator |

#### Processing

1. Collect all PII entity timestamps from detection result
2. Add configurable buffer (default 50ms) around each entity
3. Merge overlapping time ranges
4. Generate single FFmpeg filter chain
5. Execute single-pass processing

```bash
# Silence mode example
ffmpeg -i input.wav \
  -af "volume=enable='between(t,2.3,4.1)':volume=0,volume=enable='between(t,7.8,9.2)':volume=0" \
  output.wav
```

#### Buffer Configuration

| Setting | Default | Range | Purpose |
|---------|---------|-------|---------|
| `buffer_ms` | 50 | 0-500 | Padding around entity timestamps |

Buffer accounts for alignment imprecision and natural speech patterns (PII often has leading/trailing sounds).

### API Design

#### Request Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `pii_detection` | boolean | false | Enable PII detection |
| `pii_detection_tier` | string | standard | fast/standard/thorough |
| `pii_entity_types` | string[] | null | Entity types to detect (null = defaults) |
| `redact_pii` | boolean | false | Generate redacted transcript |
| `redact_pii_audio` | boolean | false | Generate redacted audio |
| `pii_redaction_mode` | string | silence | silence/beep |
| `pii_buffer_ms` | integer | 50 | Buffer around entities (ms) |

#### Example Request

```bash
curl -X POST http://localhost:8000/v1/audio/transcriptions \
  -H "Authorization: Bearer dk_xxx" \
  -F "file=@call.mp3" \
  -F "pii_detection=true" \
  -F "pii_detection_tier=standard" \
  -F "pii_entity_types=credit_card_number,phone_number,name" \
  -F "redact_pii=true" \
  -F "redact_pii_audio=true" \
  -F "pii_redaction_mode=silence"
```

#### Job Response

```json
{
  "id": "job_abc123",
  "status": "completed",
  "pii": {
    "enabled": true,
    "detection_tier": "standard",
    "entities_detected": 5,
    "entity_summary": {
      "credit_card_number": 1,
      "phone_number": 2,
      "name": 2
    },
    "redacted_audio_available": true
  },
  "transcript": {
    "text": "Hello [NAME], your card ending [CREDIT_CARD] was charged...",
    "segments": [
      {
        "start": 0.0,
        "end": 5.2,
        "text": "Hello [NAME], your card ending [CREDIT_CARD] was charged...",
        "speaker": "SPEAKER_00",
        "words": [
          {"text": "Hello", "start": 0.0, "end": 0.5},
          {"text": "[NAME]", "start": 0.6, "end": 1.2, "pii": true},
          ...
        ]
      }
    ]
  },
  "entities": [
    {
      "type": "credit_card_number",
      "category": "pci",
      "start_offset": 42,
      "end_offset": 58,
      "start_time": 2.34,
      "end_time": 4.12,
      "confidence": 0.98,
      "speaker": "SPEAKER_01",
      "redacted_value": "****7890"
    }
  ]
}
```

#### Additional Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/v1/pii/entity-types` | GET | List available entity types with categories |
| `/v1/audio/transcriptions/{id}/audio/redacted` | GET | Download redacted audio |
| `/v1/audio/transcriptions/{id}/transcript/unredacted` | GET | Get unredacted (if retained) |

#### Entity Types Response

```json
{
  "entity_types": [
    {
      "id": "credit_card_number",
      "category": "pci",
      "display_name": "Credit Card Number",
      "description": "Payment card numbers (Visa, Mastercard, Amex, etc.)",
      "detection_method": "regex+luhn",
      "is_default": true,
      "available_in_tiers": ["fast", "standard", "thorough"]
    }
  ]
}
```

### ElevenLabs Compatibility

PII parameters are accepted as Dalston extensions on the ElevenLabs-compatible endpoint:

```bash
curl -X POST http://localhost:8000/v1/speech-to-text \
  -H "xi-api-key: dk_xxx" \
  -F "file=@call.mp3" \
  -F "pii_detection=true" \
  -F "redact_pii=true"
```

ElevenLabs SDKs ignore unknown fields, so existing integrations continue to work.

### Retention Integration

PII detection integrates with the M25 retention system. The default behavior is **redact and delete**:

| Stage | Data State |
|-------|------------|
| Pipeline processing | Unredacted transcript in memory |
| PII detection | Entities detected, redacted text generated |
| Audio redaction | Redacted audio file produced |
| Pipeline cleanup | Unredacted content deleted |
| **Persistent storage** | **Only redacted outputs persist** |

#### Retention Behavior by Policy

| Policy | Unredacted Transcript | Redacted Transcript | Redacted Audio |
|--------|----------------------|---------------------|----------------|
| `default` | Deleted immediately | Kept per policy | Kept per policy |
| `zero-retention` | Never stored | Deleted immediately | Deleted immediately |
| `keep` | Available if `retain_unredacted=true` | Kept indefinitely | Kept indefinitely |

#### Audit Events

| Event | Trigger | Detail Fields |
|-------|---------|---------------|
| `pii.detected` | PII detection completes | `{entity_count, categories, tier}` |
| `pii.audio_redacted` | Audio redaction completes | `{entities_redacted, mode, duration}` |
| `pii.unredacted_deleted` | Unredacted content deleted | `{job_id}` |

### Engine Specifications

#### PII Detection Engine

```yaml
id: pii-presidio
stage: pii_detect
name: PII Detection (Presidio + GLiNER)
version: 1.0.0

container:
  gpu: optional
  memory: 4G
  model_cache: /models

capabilities:
  languages: [all]
  detection_tiers: [fast, standard, thorough]

input:
  required:
    - transcription.segments
    - alignment.words (for audio timestamps)
  optional:
    - diarization.turns (for speaker attribution)

output:
  - pii_detection.entities
  - pii_detection.redacted_text
  - pii_detection.entity_count_by_type
```

#### Audio Redaction Engine

```yaml
id: audio-redactor
stage: redact
name: Audio Redaction (FFmpeg)
version: 1.0.0

container:
  gpu: none
  memory: 2G

capabilities:
  redaction_modes: [silence, beep]

input:
  required:
    - audio_uri (original audio)
    - pii_detection.entities

output:
  - audio_redaction.redacted_audio_uri
  - audio_redaction.redaction_map
```

---

## Plan

### Files to Create

| File | Purpose |
|------|---------|
| `engines/detect/pii-presidio/` | PII detection engine container |
| `engines/redact/audio-redactor/` | Audio redaction engine container |
| `dalston/gateway/api/v1/pii.py` | PII entity types endpoint |
| `alembic/versions/xxx_add_pii_columns.py` | PII columns on jobs table |
| `alembic/versions/xxx_create_pii_entity_types.py` | Entity types reference table |

### Files to Modify

| File | Change |
|------|--------|
| `dalston/db/models.py` | Add PII columns to JobModel |
| `dalston/common/models.py` | Add PII enums and dataclasses |
| `dalston/gateway/models/requests.py` | Add PII parameters |
| `dalston/gateway/models/responses.py` | Add PII response models |
| `dalston/gateway/api/v1/transcription.py` | Accept PII params, add endpoints |
| `dalston/gateway/api/v1/speech_to_text.py` | Accept PII params |
| `dalston/gateway/api/v1/router.py` | Mount PII router |
| `dalston/orchestrator/dag_builder.py` | Add PII tasks to DAG |
| `engines/merge/final-merger/engine.py` | Include PII in merged output |
| `docker-compose.yml` | Add PII engine services |

### Implementation Tasks

See [M26: PII Detection & Audio Redaction](../plan/milestones/M26-pii-detection-redaction.md) for detailed implementation plan.

### Verification

1. **Entity detection accuracy**: Test against corpus with known PII positions
2. **Luhn validation**: Credit cards with valid/invalid checksums
3. **IBAN validation**: Valid/invalid mod-97 checksums
4. **Audio redaction**: Verify silence/beep at correct timestamps
5. **Buffer handling**: Verify padding and overlap merging
6. **Retention integration**: Verify unredacted content deleted
7. **API compatibility**: ElevenLabs endpoint accepts PII params

---

## Future Phases

### Phase 2: Real-Time PII Detection

After M6 (Real-Time MVP):

1. Pattern-based PII detection in WebSocket buffer window
2. PII masking in emitted transcript text
3. PII event WebSocket messages
4. Stream-to-storage redaction

### Phase 3: Advanced Features

1. LLM-based contextual detection (thorough tier)
2. Custom recognizer API
3. PII analytics dashboard
4. Compliance presets (PCI-DSS, HIPAA, GDPR)
5. PII Vault with role-based reconstruct
6. Pluggable third-party engines via DIR contract
