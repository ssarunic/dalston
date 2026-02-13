# M26: PII Detection & Audio Redaction

|               |                                                                                           |
| ------------- | ----------------------------------------------------------------------------------------- |
| **Goal**      | Automatically detect PII in transcripts and redact from both text and source audio       |
| **Duration**  | 8-10 days                                                                                 |
| **Dependencies** | M3 (Word Timestamps), M4 (Speaker Diarization), M25 (Data Retention)                   |
| **Deliverable** | PII detection engine, audio redaction, dual output, configurable entity types          |
| **Status**    | Not Started                                                                               |

## User Story

> *"As a compliance officer at a regulated organization, I want to automatically detect and redact PII from call recordings so that sensitive data never persists beyond pipeline processing, keeping our infrastructure out of PCI/HIPAA audit scope."*

> *"As an API user, I want both redacted and unredacted transcripts available in a single API response, with entity positions marked, so downstream systems can choose the appropriate version based on authorization level."*

---

## Overview

```text
┌─────────────────────────────────────────────────────────────────────────────────┐
│                         PII DETECTION & AUDIO REDACTION                          │
│                                                                                  │
│  ┌─────────────────────────────────────────────────────────────────────────────┐│
│  │                         PIPELINE INTEGRATION                                 ││
│  │                                                                              ││
│  │   TRANSCRIBE → ALIGN → DIARIZE → PII DETECT → AUDIO REDACT → MERGE          ││
│  │                                       │              │                       ││
│  │                                       │              │                       ││
│  │                               Entity detection   FFmpeg silence/beep         ││
│  │                               + redacted text    over PII spans              ││
│  │                                                                              ││
│  └─────────────────────────────────────────────────────────────────────────────┘│
│                                    │                                             │
│                        Detection tier selection                                  │
│                                    ▼                                             │
│  ┌─────────────────────────────────────────────────────────────────────────────┐│
│  │                         DETECTION TIERS                                      ││
│  │                                                                              ││
│  │   ┌──────────────┐  ┌──────────────┐  ┌──────────────┐                       ││
│  │   │     Fast     │  │   Standard   │  │   Thorough   │                       ││
│  │   │  Presidio    │  │  + GLiNER    │  │    + LLM     │                       ││
│  │   │  regex only  │  │  NER model   │  │  contextual  │                       ││
│  │   │   < 5ms      │  │   ~100ms     │  │    1-3s      │                       ││
│  │   └──────────────┘  └──────────────┘  └──────────────┘                       ││
│  │                                                                              ││
│  └─────────────────────────────────────────────────────────────────────────────┘│
│                                    │                                             │
│                           Redaction strategy                                     │
│                                    ▼                                             │
│  ┌─────────────────────────────────────────────────────────────────────────────┐│
│  │                         DATA FLOW (Redact & Delete)                          ││
│  │                                                                              ││
│  │   Audio ingested ──► Transcript + PII detected ──► Redacted outputs          ││
│  │        │                        │                        │                   ││
│  │        │                        │                        │                   ││
│  │   Temp storage            Pipeline memory           Persistent storage       ││
│  │   (deleted)               (discarded)               (no raw PII)             ││
│  │                                                                              ││
│  └─────────────────────────────────────────────────────────────────────────────┘│
│                                                                                  │
└─────────────────────────────────────────────────────────────────────────────────┘
```

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

### Detection Tiers

Three tiers allow customers to trade off speed, accuracy, and resources:

| Tier | Engine | Compute | Latency | Coverage |
|------|--------|---------|---------|----------|
| `fast` | Presidio regex + checksum | CPU | < 5ms | Cards, SSNs, IBANs, emails, phones, IPs |
| `standard` | Presidio + GLiNER | CPU | ~100ms | + Names, orgs, locations, medical |
| `thorough` | Presidio + GLiNER + LLM | GPU/API | 1-3s | + Contextual/indirect PII |

---

## Steps

### 26.1: Database Schema

**Deliverables:**

- Add PII-related columns to `jobs` table
- Create `pii_entity_types` reference table
- Add indexes for PII filtering queries

**Schema highlights:**

```sql
-- Jobs table - PII columns
ALTER TABLE jobs ADD COLUMN pii_detection_enabled BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE jobs ADD COLUMN pii_detection_tier VARCHAR(20);
ALTER TABLE jobs ADD COLUMN pii_entity_types TEXT[];
ALTER TABLE jobs ADD COLUMN pii_redact_audio BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE jobs ADD COLUMN pii_redaction_mode VARCHAR(20);
ALTER TABLE jobs ADD COLUMN pii_entities_detected INTEGER;
ALTER TABLE jobs ADD COLUMN pii_redacted_audio_uri TEXT;

-- PII entity types reference (for validation)
CREATE TABLE pii_entity_types (
    id VARCHAR(50) PRIMARY KEY,
    category VARCHAR(20) NOT NULL,
    display_name VARCHAR(100) NOT NULL,
    description TEXT,
    detection_method VARCHAR(50) NOT NULL,
    is_default BOOLEAN NOT NULL DEFAULT false
);

CREATE INDEX idx_jobs_pii_enabled ON jobs(pii_detection_enabled)
    WHERE pii_detection_enabled = true;
```

---

### 26.2: Common Types & Enums

**Deliverables:**

- Add `PIIDetectionTier` enum (`fast`, `standard`, `thorough`)
- Add `PIIRedactionMode` enum (`silence`, `beep`)
- Add `PIIEntityCategory` enum (`pii`, `pci`, `phi`)
- Add `PIIEntity` and `PIIAnnotation` models
- Add types to SDK and web console

**Models:**

```python
class PIIDetectionTier(str, Enum):
    FAST = "fast"           # Presidio regex only
    STANDARD = "standard"   # Presidio + GLiNER
    THOROUGH = "thorough"   # Presidio + GLiNER + LLM

class PIIRedactionMode(str, Enum):
    SILENCE = "silence"     # Replace with silence (volume=0)
    BEEP = "beep"           # Replace with 1kHz tone

class PIIEntityCategory(str, Enum):
    PII = "pii"             # Personal: name, email, phone, SSN, etc.
    PCI = "pci"             # Payment: credit card, IBAN, CVV, etc.
    PHI = "phi"             # Health: MRN, conditions, medications, etc.

@dataclass
class PIIEntity:
    entity_type: str                # e.g., "credit_card_number"
    category: PIIEntityCategory     # pii, pci, phi
    start_offset: int               # Character offset in text
    end_offset: int                 # Character offset in text
    start_time: float               # Audio time (seconds)
    end_time: float                 # Audio time (seconds)
    confidence: float               # Detection confidence 0.0-1.0
    speaker: str | None             # Speaker ID if diarized
    redacted_value: str             # e.g., "****7890"

@dataclass
class PIIDetectionResult:
    entities: list[PIIEntity]
    redacted_text: str
    entity_count_by_type: dict[str, int]
    detection_tier: PIIDetectionTier
    processing_time_ms: int
```

---

### 26.3: PII Detection Engine Container

**Deliverables:**

- Create `engines/detect/pii-presidio/` directory structure
- Implement Presidio-based detection with GLiNER backend
- Add Luhn checksum validation for credit cards
- Add IBAN mod-97 validation
- Support all entity types from PRD

**Directory structure:**

```
engines/detect/pii-presidio/
├── Dockerfile
├── requirements.txt
├── engine.yaml
├── engine.py
├── recognizers/
│   ├── __init__.py
│   ├── credit_card.py      # Luhn validation
│   ├── iban.py             # ISO 13616 mod-97
│   ├── ssn.py              # US SSN with context
│   ├── phone.py            # International formats
│   └── regional/
│       ├── jmbg.py         # Serbian/Yugoslav national ID
│       └── oib.py          # Croatian personal ID
└── gliner_backend.py       # GLiNER as Presidio NER backend
```

**Engine implementation:**

```python
class PIIDetectionEngine(Engine):
    """PII detection using Presidio + GLiNER."""

    def __init__(self):
        super().__init__()
        self.analyzer = None
        self.gliner_model = None

    def load_model(self, config: dict):
        tier = config.get("detection_tier", "standard")

        # Always load Presidio with regex recognizers
        self.analyzer = AnalyzerEngine()
        self._register_custom_recognizers()

        # Load GLiNER for standard/thorough tiers
        if tier in ("standard", "thorough"):
            self.gliner_model = GLiNER.from_pretrained("urchade/gliner_multi-v2.1")

    def process(self, input: TaskInput) -> TaskOutput:
        self.load_model(input.config)

        transcript = input.previous_outputs["transcription"]
        entity_types = input.config.get("entity_types", self._default_entities())

        # Run detection
        entities = self._detect_entities(transcript, entity_types)

        # Generate redacted text
        redacted_text = self._redact_text(transcript["text"], entities)

        return TaskOutput(data={
            "entities": [e.to_dict() for e in entities],
            "redacted_text": redacted_text,
            "entity_count_by_type": self._count_by_type(entities),
            "detection_tier": input.config.get("detection_tier", "standard"),
        })
```

**engine.yaml:**

```yaml
id: pii-presidio
stage: detect
name: PII Detection (Presidio + GLiNER)
version: 1.0.0
description: |
  Detects personally identifiable information using Microsoft Presidio
  with GLiNER for ML-based entity recognition.

container:
  gpu: optional                    # GPU accelerates GLiNER
  memory: 4G
  model_cache: /models

capabilities:
  languages:
    - all                          # Regex works on any language
  detection_tiers:
    - fast
    - standard
    - thorough

config_schema:
  type: object
  properties:
    detection_tier:
      type: string
      enum: [fast, standard, thorough]
      default: standard
    entity_types:
      type: array
      items:
        type: string
      default: null                # null = all default entities
    confidence_threshold:
      type: number
      default: 0.5
      minimum: 0.0
      maximum: 1.0
```

---

### 26.4: Audio Redaction Engine

**Deliverables:**

- Create `engines/redact/audio-redactor/` directory structure
- Implement FFmpeg-based audio redaction
- Support silence and beep modes
- Configurable buffer padding around entities

**Directory structure:**

```
engines/redact/audio-redactor/
├── Dockerfile
├── requirements.txt
├── engine.yaml
└── engine.py
```

**Engine implementation:**

```python
class AudioRedactionEngine(Engine):
    """Audio redaction using FFmpeg."""

    def process(self, input: TaskInput) -> TaskOutput:
        audio_path = input.audio_path
        entities = input.previous_outputs["pii_detection"]["entities"]
        mode = input.config.get("redaction_mode", "silence")
        buffer_ms = input.config.get("buffer_ms", 50)

        # Build FFmpeg filter chain
        filter_chain = self._build_filter_chain(entities, mode, buffer_ms)

        # Execute single-pass FFmpeg command
        output_path = self._redact_audio(audio_path, filter_chain)

        return TaskOutput(
            data={
                "redaction_mode": mode,
                "buffer_ms": buffer_ms,
                "entities_redacted": len(entities),
                "redaction_map": self._build_redaction_map(entities, buffer_ms),
            },
            artifacts={"redacted_audio": output_path}
        )

    def _build_filter_chain(
        self,
        entities: list[dict],
        mode: str,
        buffer_ms: int
    ) -> str:
        """Build FFmpeg filter for all PII spans in single pass."""
        buffer_sec = buffer_ms / 1000.0
        ranges = []

        for entity in entities:
            start = max(0, entity["start_time"] - buffer_sec)
            end = entity["end_time"] + buffer_sec
            ranges.append((start, end))

        # Merge overlapping ranges
        merged = self._merge_ranges(ranges)

        if mode == "silence":
            # Chain of volume=0 filters for each range
            filters = []
            for start, end in merged:
                filters.append(f"volume=enable='between(t,{start},{end})':volume=0")
            return ",".join(filters)
        else:  # beep
            # Generate 1kHz tone and mix over PII spans
            return self._build_beep_filter(merged)
```

**engine.yaml:**

```yaml
id: audio-redactor
stage: redact
name: Audio Redaction (FFmpeg)
version: 1.0.0
description: |
  Redacts audio by replacing PII segments with silence or beep tones.
  Uses FFmpeg for efficient single-pass processing.

container:
  gpu: none
  memory: 2G

capabilities:
  redaction_modes:
    - silence
    - beep

config_schema:
  type: object
  properties:
    redaction_mode:
      type: string
      enum: [silence, beep]
      default: silence
    buffer_ms:
      type: integer
      default: 50
      minimum: 0
      maximum: 500
```

---

### 26.5: Update DAG Builder

**Deliverables:**

- Add PII detection task creation based on `pii_detection` parameter
- Add audio redaction task creation based on `redact_pii_audio` parameter
- PII detection depends on alignment (for word timestamps)
- Audio redaction depends on PII detection
- Both tasks are optional (don't fail job if not requested)

**DAG structure with PII:**

```
prepare → transcribe → align → diarize ─┬─→ pii_detect (optional)
                                         │        │
                                         │        └─→ audio_redact (optional)
                                         │                  │
                                         └──────────────────┴─→ merge
```

**Implementation:**

```python
def build_dag(job: Job) -> list[Task]:
    tasks = []

    # ... existing stages ...

    # PII Detection (optional, after alignment)
    if job.pii_detection_enabled:
        pii_task = Task(
            stage="detect",
            engine_id="pii-presidio",
            depends_on=[align_task.id, diarize_task.id] if diarize_task else [align_task.id],
            required=True,  # If requested, it should succeed
            config={
                "detection_tier": job.pii_detection_tier,
                "entity_types": job.pii_entity_types,
            }
        )
        tasks.append(pii_task)

        # Audio Redaction (optional, after PII detection)
        if job.pii_redact_audio:
            redact_task = Task(
                stage="redact",
                engine_id="audio-redactor",
                depends_on=[pii_task.id],
                required=True,
                config={
                    "redaction_mode": job.pii_redaction_mode,
                }
            )
            tasks.append(redact_task)

    return tasks
```

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
  -F "pii_detection_tier=standard" \
  -F "redact_pii=true" \
  -F "redact_pii_audio=true" \
  -F "pii_redaction_mode=silence"
```

**Job response with PII:**

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
    "segments": [...]
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

**New endpoints:**

```
GET /v1/pii/entity-types              List available entity types
GET /v1/audio/transcriptions/{id}/audio/redacted   Download redacted audio
GET /v1/audio/transcriptions/{id}/transcript/unredacted   Get unredacted (if retained)
```

---

### 26.7: Entity Type Registry

**Deliverables:**

- Seed database with default entity types
- Create `GET /v1/pii/entity-types` endpoint
- Support category filtering

**Default entity types:**

```python
DEFAULT_ENTITY_TYPES = [
    # PII Category (Personal)
    {"id": "name", "category": "pii", "method": "gliner", "default": True},
    {"id": "name_given", "category": "pii", "method": "gliner", "default": False},
    {"id": "name_family", "category": "pii", "method": "gliner", "default": False},
    {"id": "email_address", "category": "pii", "method": "regex", "default": True},
    {"id": "phone_number", "category": "pii", "method": "regex", "default": True},
    {"id": "ssn", "category": "pii", "method": "regex", "default": True},
    {"id": "location", "category": "pii", "method": "gliner", "default": True},
    {"id": "location_address", "category": "pii", "method": "gliner", "default": False},
    {"id": "date_of_birth", "category": "pii", "method": "gliner", "default": True},
    {"id": "age", "category": "pii", "method": "gliner", "default": False},
    {"id": "ip_address", "category": "pii", "method": "regex", "default": True},
    {"id": "driver_license", "category": "pii", "method": "regex", "default": False},
    {"id": "passport_number", "category": "pii", "method": "regex", "default": False},
    {"id": "organization", "category": "pii", "method": "gliner", "default": False},

    # PCI Category (Payment Card Industry)
    {"id": "credit_card_number", "category": "pci", "method": "regex+luhn", "default": True},
    {"id": "credit_card_cvv", "category": "pci", "method": "regex+context", "default": True},
    {"id": "credit_card_expiry", "category": "pci", "method": "regex", "default": True},
    {"id": "iban", "category": "pci", "method": "regex+checksum", "default": True},
    {"id": "bank_account", "category": "pci", "method": "gliner", "default": False},

    # PHI Category (Protected Health Information)
    {"id": "medical_record_number", "category": "phi", "method": "regex", "default": False},
    {"id": "medical_condition", "category": "phi", "method": "gliner", "default": False},
    {"id": "medication", "category": "phi", "method": "gliner", "default": False},
    {"id": "health_plan_id", "category": "phi", "method": "regex", "default": False},

    # Regional (SE European)
    {"id": "jmbg", "category": "pii", "method": "regex+checksum", "default": False},
    {"id": "oib", "category": "pii", "method": "regex+checksum", "default": False},
]
```

---

### 26.8: Merger Integration

**Deliverables:**

- Update final-merger to include PII entities in output
- Include both redacted and unredacted text
- Mark entity positions in segments
- Track redacted audio file reference

**Merged output structure:**

```json
{
  "text": "Full unredacted transcript...",
  "redacted_text": "Hello [NAME], your card ending [CREDIT_CARD]...",
  "segments": [
    {
      "start": 0.0,
      "end": 3.5,
      "text": "Hello John Smith",
      "redacted_text": "Hello [NAME]",
      "speaker": "SPEAKER_00",
      "words": [
        {"text": "Hello", "start": 0.0, "end": 0.5},
        {"text": "John", "start": 0.6, "end": 0.9, "pii": {"type": "name", "redacted": "[NAME]"}},
        {"text": "Smith", "start": 1.0, "end": 1.3, "pii": {"type": "name", "redacted": "[NAME]"}}
      ]
    }
  ],
  "entities": [...],
  "pii_metadata": {
    "detection_tier": "standard",
    "entities_detected": 5,
    "redacted_audio_uri": "s3://bucket/jobs/abc/audio/redacted.wav"
  }
}
```

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

```python
# Example SDK usage
result = client.transcribe(
    file="call.mp3",
    pii_detection=True,
    pii_detection_tier="standard",
    redact_pii=True,
    redact_pii_audio=True,
)

print(f"Detected {len(result.entities)} PII entities")
print(f"Redacted text: {result.redacted_text}")

# Download redacted audio
client.download_redacted_audio(result.id, "redacted_call.mp3")
```

**CLI (`cli/dalston_cli/`):**

- Add `--pii-detection` flag to `transcribe` command
- Add `--pii-tier` flag (fast/standard/thorough)
- Add `--redact-audio` flag
- Add `dalston pii entity-types` command

```bash
dalston transcribe call.mp3 \
  --pii-detection \
  --pii-tier standard \
  --redact-audio \
  --output-redacted redacted_call.mp3
```

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

```bash
# Submit with PII detection
JOB_ID=$(curl -s -X POST http://localhost:8000/v1/audio/transcriptions \
  -H "Authorization: Bearer dk_xxx" \
  -F "file=@call_with_pii.mp3" \
  -F "pii_detection=true" \
  -F "pii_detection_tier=standard" \
  -F "redact_pii=true" \
  -F "redact_pii_audio=true" | jq -r '.id')

# Poll for completion
curl http://localhost:8000/v1/audio/transcriptions/$JOB_ID \
  -H "Authorization: Bearer dk_xxx" | jq '.status'

# Check detected entities
curl http://localhost:8000/v1/audio/transcriptions/$JOB_ID \
  -H "Authorization: Bearer dk_xxx" | jq '.entities'

# Verify redacted text
curl http://localhost:8000/v1/audio/transcriptions/$JOB_ID/transcript \
  -H "Authorization: Bearer dk_xxx" | jq '.redacted_text'

# Download redacted audio
curl -O http://localhost:8000/v1/audio/transcriptions/$JOB_ID/audio/redacted \
  -H "Authorization: Bearer dk_xxx"

# List available entity types
curl http://localhost:8000/v1/pii/entity-types \
  -H "Authorization: Bearer dk_xxx" | jq '.entity_types[] | select(.category == "pci")'

# Test fast tier (regex only)
curl -X POST http://localhost:8000/v1/audio/transcriptions \
  -H "Authorization: Bearer dk_xxx" \
  -F "file=@call.mp3" \
  -F "pii_detection=true" \
  -F "pii_detection_tier=fast" \
  -F "pii_entity_types=credit_card_number,iban,phone_number"
```

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

## Files Changed

| File | Description |
|------|-------------|
| `alembic/versions/xxx_add_pii_columns.py` | Migration for PII columns on jobs |
| `alembic/versions/xxx_create_pii_entity_types.py` | Migration for entity types table |
| `dalston/db/models.py` | Add PII columns to JobModel, PIIEntityTypeModel |
| `dalston/common/models.py` | Add PII enums and data classes |
| `dalston/gateway/models/requests.py` | Add PII params to job submission |
| `dalston/gateway/models/responses.py` | Add PIIInfo, PIIEntity response models |
| `dalston/gateway/api/v1/transcription.py` | Accept PII params, add redacted audio endpoint |
| `dalston/gateway/api/v1/speech_to_text.py` | Accept PII params (ElevenLabs compat) |
| `dalston/gateway/api/v1/pii.py` | New: entity types endpoint |
| `dalston/gateway/api/v1/router.py` | Mount PII router |
| `dalston/gateway/services/jobs.py` | Handle PII job configuration |
| `dalston/orchestrator/dag_builder.py` | Add PII detection and audio redaction tasks |
| `engines/detect/pii-presidio/` | New: PII detection engine |
| `engines/redact/audio-redactor/` | New: Audio redaction engine |
| `engines/merge/final-merger/engine.py` | Include PII entities in output |
| `docker-compose.yml` | Add PII engine services |
| `sdk/dalston_sdk/types.py` | Add PII types |
| `sdk/dalston_sdk/client.py` | Add PII parameters and methods |
| `cli/dalston_cli/commands/transcribe.py` | Add PII flags |
| `cli/dalston_cli/commands/pii.py` | New: PII entity types command |
| `web/src/api/types.ts` | Add PII types |
| `web/src/pages/JobSubmit.tsx` | Add PII configuration UI |
| `web/src/pages/JobDetail.tsx` | Show PII results |
| `web/src/components/PIIEntityList.tsx` | New: entity list component |
| `web/src/components/RedactedTranscriptViewer.tsx` | New: toggle redacted/unredacted |

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
