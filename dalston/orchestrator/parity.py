"""Parity comparison helpers for PII pipeline vs post-process modes (M67).

Compares the redacted transcript and audio outputs produced by the two
PII processing modes to validate equivalence.

Allowed variance:
- Metadata timing fields may differ by up to ``TIMING_TOLERANCE_MS``
  milliseconds due to async scheduling.
- Ordering of PII entities within a segment is normalised before comparison.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Maximum allowed timing difference (in milliseconds) for metadata fields
TIMING_TOLERANCE_MS = 50


@dataclass
class ParityResult:
    """Result of a parity comparison between pipeline and post-process outputs."""

    is_equivalent: bool
    text_match: bool
    entity_match: bool
    audio_match: bool | None = None  # None if audio redaction not applicable
    differences: list[str] = field(default_factory=list)

    def summary(self) -> str:
        status = "PASS" if self.is_equivalent else "FAIL"
        parts = [f"Parity: {status}"]
        if not self.text_match:
            parts.append("text_mismatch")
        if not self.entity_match:
            parts.append("entity_mismatch")
        if self.audio_match is False:
            parts.append("audio_mismatch")
        if self.differences:
            parts.append(f"differences={len(self.differences)}")
        return " | ".join(parts)


def compare_redacted_text(
    pipeline_text: str,
    post_process_text: str,
) -> tuple[bool, list[str]]:
    """Compare redacted transcript text from both modes.

    Args:
        pipeline_text: Redacted text from pipeline mode.
        post_process_text: Redacted text from post-process mode.

    Returns:
        Tuple of (match, list of difference descriptions).
    """
    if pipeline_text == post_process_text:
        return True, []

    differences: list[str] = []

    # Normalise whitespace for comparison
    norm_pipeline = " ".join(pipeline_text.split())
    norm_post = " ".join(post_process_text.split())

    if norm_pipeline == norm_post:
        differences.append("whitespace_only_difference")
        return True, differences

    # Find first divergence point
    for i, (a, b) in enumerate(zip(norm_pipeline, norm_post, strict=False)):
        if a != b:
            ctx_start = max(0, i - 20)
            ctx_end = i + 20
            differences.append(
                f"first_divergence_at_char_{i}: "
                f"pipeline=...{norm_pipeline[ctx_start:ctx_end]}... "
                f"post_process=...{norm_post[ctx_start:ctx_end]}..."
            )
            break

    if len(norm_pipeline) != len(norm_post):
        differences.append(
            f"length_mismatch: pipeline={len(norm_pipeline)} "
            f"post_process={len(norm_post)}"
        )

    return False, differences


def _normalise_entity(entity: dict[str, Any]) -> dict[str, Any]:
    """Normalise a PII entity for comparison.

    Strips timing-sensitive fields and sorts by text offset.
    """
    return {
        "entity_type": entity.get("entity_type", ""),
        "start_offset": entity.get("start_offset", 0),
        "end_offset": entity.get("end_offset", 0),
        "redacted_value": entity.get("redacted_value", ""),
        "category": entity.get("category", ""),
    }


def compare_pii_entities(
    pipeline_entities: list[dict[str, Any]],
    post_process_entities: list[dict[str, Any]],
) -> tuple[bool, list[str]]:
    """Compare PII entities detected by both modes.

    Entities are normalised (timing-insensitive fields only) and sorted
    before comparison.

    Args:
        pipeline_entities: PII entities from pipeline mode.
        post_process_entities: PII entities from post-process mode.

    Returns:
        Tuple of (match, list of difference descriptions).
    """
    differences: list[str] = []

    norm_pipeline = sorted(
        [_normalise_entity(e) for e in pipeline_entities],
        key=lambda e: (e["start_offset"], e["entity_type"]),
    )
    norm_post = sorted(
        [_normalise_entity(e) for e in post_process_entities],
        key=lambda e: (e["start_offset"], e["entity_type"]),
    )

    if norm_pipeline == norm_post:
        return True, []

    if len(norm_pipeline) != len(norm_post):
        differences.append(
            f"entity_count_mismatch: pipeline={len(norm_pipeline)} "
            f"post_process={len(norm_post)}"
        )

    # Find specific mismatches
    max_len = max(len(norm_pipeline), len(norm_post))
    for i in range(max_len):
        p = norm_pipeline[i] if i < len(norm_pipeline) else None
        pp = norm_post[i] if i < len(norm_post) else None
        if p != pp:
            differences.append(
                f"entity_mismatch_at_{i}: pipeline={p} post_process={pp}"
            )
            if len(differences) >= 10:
                differences.append("... (truncated)")
                break

    return False, differences


def compare_audio_redaction(
    pipeline_redaction_map: list[list[float]],
    post_process_redaction_map: list[list[float]],
    tolerance_ms: float = TIMING_TOLERANCE_MS,
) -> tuple[bool, list[str]]:
    """Compare audio redaction maps from both modes.

    Each redaction map entry is ``[start_time, end_time, ...]``.
    Timing differences within ``tolerance_ms`` are accepted.

    Args:
        pipeline_redaction_map: Redaction intervals from pipeline mode.
        post_process_redaction_map: Redaction intervals from post-process mode.
        tolerance_ms: Allowed timing tolerance in milliseconds.

    Returns:
        Tuple of (match, list of difference descriptions).
    """
    differences: list[str] = []
    tolerance_s = tolerance_ms / 1000.0

    if len(pipeline_redaction_map) != len(post_process_redaction_map):
        differences.append(
            f"redaction_count_mismatch: pipeline={len(pipeline_redaction_map)} "
            f"post_process={len(post_process_redaction_map)}"
        )
        return False, differences

    for i, (p_entry, pp_entry) in enumerate(
        zip(pipeline_redaction_map, post_process_redaction_map, strict=True)
    ):
        if len(p_entry) < 2 or len(pp_entry) < 2:
            differences.append(f"invalid_entry_at_{i}")
            continue

        start_diff = abs(p_entry[0] - pp_entry[0])
        end_diff = abs(p_entry[1] - pp_entry[1])

        if start_diff > tolerance_s or end_diff > tolerance_s:
            differences.append(
                f"timing_mismatch_at_{i}: "
                f"pipeline=[{p_entry[0]:.3f}, {p_entry[1]:.3f}] "
                f"post_process=[{pp_entry[0]:.3f}, {pp_entry[1]:.3f}] "
                f"(start_diff={start_diff * 1000:.1f}ms, end_diff={end_diff * 1000:.1f}ms)"
            )

    return len(differences) == 0, differences


def compare_pii_outputs(
    pipeline_output: dict[str, Any],
    post_process_output: dict[str, Any],
) -> ParityResult:
    """Full parity comparison of PII outputs from both modes.

    Compares redacted text, PII entities, and optionally audio redaction
    maps.

    Args:
        pipeline_output: Output from pipeline mode (containing
            ``redacted_text``, ``pii_entities``, optionally ``redaction_map``).
        post_process_output: Output from post-process mode.

    Returns:
        ParityResult with detailed comparison results.
    """
    differences: list[str] = []

    # Compare redacted text
    text_match, text_diffs = compare_redacted_text(
        pipeline_output.get("redacted_text", ""),
        post_process_output.get("redacted_text", ""),
    )
    differences.extend(text_diffs)

    # Compare PII entities
    entity_match, entity_diffs = compare_pii_entities(
        pipeline_output.get("pii_entities", []),
        post_process_output.get("pii_entities", []),
    )
    differences.extend(entity_diffs)

    # Compare audio redaction (if applicable)
    audio_match: bool | None = None
    p_redaction = pipeline_output.get("redaction_map")
    pp_redaction = post_process_output.get("redaction_map")
    if p_redaction is not None and pp_redaction is not None:
        audio_match, audio_diffs = compare_audio_redaction(p_redaction, pp_redaction)
        differences.extend(audio_diffs)
    elif p_redaction is not None or pp_redaction is not None:
        audio_match = False
        differences.append(
            "audio_redaction_presence_mismatch: "
            f"pipeline={'present' if p_redaction else 'absent'} "
            f"post_process={'present' if pp_redaction else 'absent'}"
        )

    is_equivalent = text_match and entity_match and (audio_match is not False)

    return ParityResult(
        is_equivalent=is_equivalent,
        text_match=text_match,
        entity_match=entity_match,
        audio_match=audio_match,
        differences=differences,
    )
