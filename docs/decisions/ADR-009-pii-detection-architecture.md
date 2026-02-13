# ADR-009: PII Detection & Audio Redaction Architecture

| | |
|---|---|
| **Status** | Accepted |
| **Date** | February 2026 |
| **Deciders** | Dalston Core Team |
| **Related PRD** | PII Detection & Audio Redaction v1.1 |

## Context

Dalston's regulated-industry customers (banking, healthcare, insurance, legal) require the ability to automatically identify personally identifiable information (PII) in transcripts and redact it from both text and source audio. This is the most requested feature for self-hosted deployments where compliance requirements prohibit sending sensitive audio to cloud transcription providers.

Key requirements:

1. **Single API call**: Transcription, PII detection, and redaction in one request
2. **Both text and audio redaction**: Redacted transcript AND redacted audio file
3. **Configurable entity types**: Different industries care about different PII categories
4. **Fully self-hosted**: No audio or transcript data sent to external APIs
5. **Compliance-friendly**: Support PCI DSS, HIPAA, and GDPR data minimization

This ADR addresses four key architectural decisions:

1. Where in the pipeline should PII detection run?
2. What detection approach provides the best speed/accuracy trade-off?
3. How should unredacted data be handled after pipeline processing?
4. How should audio redaction be implemented?

---

## Decision 1: Pipeline Stage Ordering

### Options Considered

**A: PII detection before alignment**

- Pro: Could skip alignment for non-PII text, saving compute
- Con: Cannot provide audio timestamps for detected entities
- Con: Audio redaction would require imprecise estimation

**B: PII detection after alignment, before diarization**

- Pro: Has word timestamps for audio redaction
- Con: Missing speaker context for compliance workflows

**C: PII detection after alignment AND diarization (Selected)**

- Pro: Has precise word timestamps for audio redaction
- Pro: Knows which speaker disclosed PII (compliance value)
- Pro: All context available for accurate detection
- Con: Adds latency (must wait for diarization)

### Decision

**Option C: PII detection runs after alignment and diarization.**

Pipeline order: `TRANSCRIBE → ALIGN → DIARIZE → PII_DETECT → AUDIO_REDACT → MERGE`

### Rationale

1. **Audio redaction requires precise timestamps.** To redact audio, we must know exactly when each PII entity was spoken. The alignment stage (WhisperX) provides word-level timestamps that map transcript positions to audio time ranges. Without alignment, audio redaction would require imprecise heuristics.

2. **Speaker attribution is compliance-critical.** Knowing which speaker disclosed PII is valuable for compliance workflows. If a customer reads their credit card number, the compliance system needs to distinguish this from an agent doing so (potential policy violation). Diarization provides this context.

3. **Audio redaction must be last.** Audio redaction is a destructive operation that produces a new audio file. All analytical stages must complete before the audio is modified. The original audio is preserved separately; the redacted version is a derivative output.

4. **Latency is acceptable for batch.** Batch transcription already takes seconds to minutes. The additional latency from running PII detection after diarization is negligible compared to the total pipeline time.

---

## Decision 2: Detection Tier Architecture

### Options Considered

**A: Single detection method for all use cases**

- Pro: Simple to implement and maintain
- Con: Either too slow for high-volume use cases or too inaccurate for compliance

**B: Multiple detection methods, user selects one**

- Pro: Users choose based on their speed/accuracy requirements
- Con: Users may not understand trade-offs

**C: Tiered detection with clear profiles (Selected)**

- Pro: Named tiers with documented trade-offs
- Pro: Easy to understand: "fast" for speed, "thorough" for accuracy
- Pro: Can upgrade tier without changing integration code

### Decision

**Option C: Three detection tiers with progressive capability.**

| Tier | Components | Compute | Latency | Use Case |
|------|------------|---------|---------|----------|
| `fast` | Presidio regex + checksum | CPU | < 5ms | High-volume, structured PII only |
| `standard` | Presidio + GLiNER NER | CPU | ~100ms | Balanced (recommended default) |
| `thorough` | Presidio + GLiNER + LLM | GPU/API | 1-3s | Maximum accuracy, contextual PII |

### Rationale

1. **80/20 rule for PII detection.** The `fast` tier covers 80%+ of regulated PII (credit cards, SSNs, IBANs, phone numbers, emails) using only regex patterns with validation (Luhn for cards, mod-97 for IBANs). These are the entities that matter most for PCI and financial compliance.

2. **GLiNER for ML-based NER.** The `standard` tier adds GLiNER, a zero-shot bidirectional transformer that handles names, organizations, and locations well across languages including non-Western names. This avoids the need for language-specific NER models.

3. **LLM for contextual PII.** The `thorough` tier adds an LLM pass to catch indirect PII that pattern matching and NER miss (e.g., "the house on the corner of 5th and Main" as a location, or "my mother's maiden name" as identity information).

4. **Progressive cost model.** Each tier has clear resource implications. Customers can start with `fast` for cost efficiency and upgrade to `standard` or `thorough` as compliance requirements demand.

---

## Decision 3: Unredacted Data Retention Strategy

### Options Considered

**A: Keep both redacted and unredacted indefinitely**

- Pro: Maximum flexibility for downstream consumers
- Con: Unredacted data creates PCI/HIPAA audit scope
- Con: Defeats the purpose of redaction

**B: Dynamic role-based redaction at read time**

- Pro: Single stored transcript, multiple views based on authorization
- Con: Requires storing unredacted data (the "toxic asset")
- Con: Every system that touches unredacted data is in compliance scope

**C: Redact and delete (Selected as default)**

- Pro: Minimizes compliance scope by eliminating unredacted data
- Pro: Satisfies GDPR data minimization principle
- Pro: PCI DSS "if you don't need it, don't store it"
- Con: Cannot retrieve original PII after pipeline completes

**D: Vault and reconstruct (Optional enterprise pattern)**

- Pro: Supports legitimate role-based access use cases
- Pro: Separates PII storage with independent security controls
- Con: Vault is in full PCI/HIPAA scope
- Con: More complex to operate

### Decision

**Option C (Redact and Delete) as default, Option D (Vault and Reconstruct) as opt-in enterprise feature.**

### Rationale

1. **Compliance-first default.** Most regulated organizations want to minimize the data they store. The default should be the safest option: produce redacted outputs and delete the unredacted content. There is no PII to protect if there is no PII stored.

2. **PCI DSS is explicit.** PCI DSS Requirement 3.1 states: "Keep cardholder data storage to a minimum." The redact-and-delete pattern directly implements this guidance.

3. **HIPAA minimum necessary.** HIPAA's minimum necessary principle is directly addressed when no PHI persists beyond pipeline execution.

4. **GDPR data minimization.** Article 5(1)(c) requires that personal data be "adequate, relevant and limited to what is necessary." Deleting unredacted content after redaction is the clearest demonstration of this principle.

5. **Vault for legitimate use cases.** Some organizations have genuine business requirements for role-based PII access (compliance team reviewing calls, QA during incident review). The vault-and-reconstruct pattern supports these use cases but requires explicit opt-in and full acceptance of the compliance implications.

### Data Flow (Default Pattern)

| Step | Action | Data State |
|------|--------|------------|
| 1 | Audio ingested | Original audio in temporary pipeline storage |
| 2 | Transcription + alignment | Unredacted transcript in pipeline memory |
| 3 | PII detection | Entities detected, redacted text generated |
| 4 | Audio redaction | Redacted audio file produced |
| 5 | Pipeline cleanup | Original audio deleted, unredacted transcript discarded |
| **6** | **Persistent storage** | **Only redacted transcript + redacted audio + entity metadata persist** |

---

## Decision 4: Audio Redaction Implementation

### Options Considered

**A: Python audio processing libraries (pydub, librosa)**

- Pro: Pure Python, easy to integrate
- Con: Memory-intensive for long audio files
- Con: Slower than native tools

**B: FFmpeg via subprocess (Selected)**

- Pro: Industry-standard audio processing
- Pro: Single-pass processing for all PII spans
- Pro: Already present in preprocessing container
- Pro: Efficient memory usage (streaming)
- Con: Requires subprocess management

**C: Custom WASM audio processor**

- Pro: Could run in browser for preview
- Con: Significant development effort
- Con: Limited format support

### Decision

**Option B: FFmpeg-based audio redaction.**

### Rationale

1. **FFmpeg is already available.** The audio preprocessing container already includes FFmpeg for format conversion. No additional dependencies needed.

2. **Single-pass efficiency.** FFmpeg's filter graph allows chaining multiple volume filters (for silence mode) or tone overlays (for beep mode) into a single processing pass, regardless of how many PII spans exist.

3. **Streaming architecture.** FFmpeg processes audio in a streaming fashion, avoiding memory issues with long recordings.

4. **Production-proven.** FFmpeg is the industry standard for audio processing. Using it for redaction is a well-understood pattern.

### Implementation

```bash
# Silence mode: Chain volume=0 filters for each PII span
ffmpeg -i input.wav -af "volume=enable='between(t,2.3,4.1)':volume=0,volume=enable='between(t,7.8,9.2)':volume=0" output.wav

# Beep mode: Generate 1kHz tone and mix over PII spans
ffmpeg -i input.wav -f lavfi -i "sine=frequency=1000" -filter_complex "[0][1]amix..." output.wav
```

Buffer padding (default 50-100ms) is applied around each entity timestamp to ensure complete redaction despite potential alignment imprecision.

---

## Consequences

### Positive

1. **Clean compliance story.** The default redact-and-delete pattern eliminates PII from persistent storage, minimizing audit scope.

2. **Flexible detection.** Three tiers allow customers to balance speed and accuracy based on their specific compliance requirements.

3. **Speaker attribution.** Running PII detection after diarization provides compliance-valuable context about who disclosed PII.

4. **Efficient audio redaction.** FFmpeg single-pass processing keeps redaction fast even for long recordings with many PII spans.

5. **Future extensibility.** The vault-and-reconstruct pattern is available for enterprise customers who need role-based access without requiring changes to the core pipeline.

### Negative

1. **Pipeline latency.** PII detection must wait for alignment and diarization to complete, adding to total processing time. Acceptable for batch, may need optimization for near-real-time use cases.

2. **No unredacted recovery (default).** With redact-and-delete, there is no way to recover original PII after pipeline completes. This is intentional but may surprise users who expect cloud-style retention.

3. **Vault complexity.** Organizations choosing the vault pattern accept significant operational complexity: separate encryption keys, access controls, backup procedures, and retention policies.

---

## Related Decisions

- [ADR-001: Storage Architecture](ADR-001-storage-architecture.md) — S3 for artifacts, PostgreSQL for metadata
- [ADR-002: Engine Isolation](ADR-002-engine-isolation.md) — Containerized engines, queue-based communication
- [ADR-003: Two-Level Queues](ADR-003-two-level-queues.md) — Jobs → Tasks DAG model
- [ADR-008: Data Retention Strategy](ADR-008-data-retention-strategy.md) — Retention policies, cleanup worker

---

## References

- [Microsoft Presidio Documentation](https://microsoft.github.io/presidio/)
- [GLiNER: Generalist Model for Named Entity Recognition](https://github.com/urchade/GLiNER)
- [PCI DSS v4.0 Requirement 3: Protect Stored Account Data](https://www.pcisecuritystandards.org/)
- [HIPAA Safe Harbor De-identification Method](https://www.hhs.gov/hipaa/for-professionals/privacy/special-topics/de-identification/)
- [GDPR Article 5(1)(c): Data Minimization](https://gdpr-info.eu/art-5-gdpr/)
