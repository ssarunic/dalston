# PII detection and audio redaction

PII detection is asynchronous post-processing in the batch pipeline. It runs
after transcript text and timing are available; optional audio redaction runs
after PII detection.

```text
prepare -> transcribe (+ optional diarize/align)
        -> pii_detect
        -> optional audio_redact
        -> orchestrator result assembly
```

There is no distributed `merge` task and no
`/transcript/unredacted` endpoint.

## Submission fields

`POST /v1/audio/transcriptions` accepts:

| Field | Meaning |
| --- | --- |
| `pii_detection` | Enable entity detection |
| `pii_entity_types` | JSON array of entity IDs; omitted uses defaults |
| `model_pii_detect` | Optional registry model ID for the `pii_detect` stage |
| `redact_pii_audio` | Produce a redacted audio artifact |
| `pii_redaction_mode` | `silence` or `beep` |

Audio redaction requires PII detection. Invalid entity IDs or a model assigned
to the wrong stage are rejected before processing.

Realtime-native requests expose similarly named options, but detection applies
to the stored transcript/artifact lifecycle. This is not a promise of
token-by-token realtime PII suppression.

## Entity types

```http
GET /v1/pii/entity-types
```

The endpoint accepts `category=pii|pci|phi` and `defaults_only=true|false`.
Use it to populate clients instead of hard-coding a list. Each entity has an ID,
category, display metadata, and default status.

## Result data

When enabled, the job result's PII section can include detected entities,
redacted text, counts, and processing metadata. An entity contains:

- entity type and category;
- transcript character offsets;
- audio start/end time;
- confidence;
- optional speaker;
- redacted value and original detected text.

Fields depend on engine output and alignment quality. Audio time cannot be more
precise than the transcript timing used by the detector.

## Transcript and audio access

The main job response is the supported transcript retrieval surface. Redacted
audio is downloaded from:

```http
GET /v1/audio/transcriptions/{job_id}/audio/redacted
```

The endpoint verifies ownership, successful completion, requested redaction,
available metadata, and retained storage. It returns a presigned download
response in distributed mode. Original audio remains available through the
normal audio endpoint only when retention and permissions allow it.

## Redaction behavior

The audio-redaction engine uses detected time ranges and replaces them with
silence or a beep. Overlapping/adjacent ranges may be normalized by the engine.
Redaction quality depends on both entity detection and word/segment alignment;
applications with strict compliance requirements must test their languages,
audio conditions, and entity categories.

Text redaction and audio redaction are separate outputs. Enabling detection
does not imply that all returned text is automatically redacted.

## Retention and security

PII metadata and unredacted artifacts follow the job's retention value. A
transient request does not produce durable downloadable artifacts. Permanent
retention should be used cautiously for raw audio, original text, and entity
metadata.

API keys need the normal job read/create permissions, and all retrieval is
tenant-scoped. Audit access and deletion events rather than logging sensitive
entity values.

## Engine contract

The detector consumes the assembled transcript/timing inputs selected by the
DAG and emits schema-valid entity/redaction data. The audio redactor consumes
source audio plus PII timing metadata. Both are ordinary task engines and must
use the current typed engine interfaces and artifact materialization contract.
