"""Output snapshot comparator for engine parity testing.

Serializes engine outputs to comparable dicts and provides diff
utilities for verifying that two engines produce equivalent results
from the same input.

Usage:
    from tests.helpers.output_snapshot import assert_outputs_equivalent

    batch_response = batch_engine.process(task_request, ctx)
    rt_response = rt_engine.transcribe(audio, language, model)

    # Compare the text and segments from both outputs
    assert_outputs_equivalent(
        normalize_batch_output(batch_response.data),
        normalize_rt_output(rt_response),
    )
"""

from __future__ import annotations

from typing import Any


def normalize_batch_output(output: Any) -> dict:
    """Normalize a Transcript (batch) to a comparable dict.

    Extracts the fields that are comparable across batch and RT outputs,
    rounding floats to avoid precision noise.

    Args:
        output: Transcript from a batch engine

    Returns:
        Dict with text, language, words (list of {text, start, end})
    """
    words = []
    for seg in output.segments:
        if seg.words:
            for w in seg.words:
                words.append(
                    {
                        "text": w.text.strip(),
                        "start": round(w.start, 2),
                        "end": round(w.end, 2),
                    }
                )

    return {
        "text": output.text.strip(),
        "language": output.language,
        "words": words,
    }


def normalize_rt_output(result: Any) -> dict:
    """Normalize a Transcript (RT) to a comparable dict.

    Args:
        result: Transcript from a realtime engine

    Returns:
        Dict with text, language, words (list of {text, start, end})
    """
    words = []
    if hasattr(result, "words") and result.words:
        for w in result.words:
            words.append(
                {
                    "text": w.word.strip(),
                    "start": round(w.start, 2),
                    "end": round(w.end, 2),
                }
            )

    return {
        "text": result.text.strip(),
        "language": result.language,
        "words": words,
    }


def diff_outputs(expected: dict, actual: dict) -> list[str]:
    """Compare two normalized outputs and return a list of differences.

    Args:
        expected: Normalized output dict (ground truth)
        actual: Normalized output dict (to verify)

    Returns:
        List of human-readable difference strings. Empty if equivalent.
    """
    diffs: list[str] = []

    if expected["text"] != actual["text"]:
        diffs.append(
            f"text mismatch: expected={expected['text']!r}, actual={actual['text']!r}"
        )

    if expected["language"] != actual["language"]:
        diffs.append(
            f"language mismatch: expected={expected['language']!r}, "
            f"actual={actual['language']!r}"
        )

    exp_words = expected["words"]
    act_words = actual["words"]

    if len(exp_words) != len(act_words):
        diffs.append(
            f"word count mismatch: expected={len(exp_words)}, actual={len(act_words)}"
        )
    else:
        for i, (ew, aw) in enumerate(zip(exp_words, act_words, strict=True)):
            if ew["text"] != aw["text"]:
                diffs.append(f"word[{i}] text: {ew['text']!r} != {aw['text']!r}")
            if abs(ew["start"] - aw["start"]) > 0.05:
                diffs.append(
                    f"word[{i}] start: {ew['start']} != {aw['start']} "
                    f"(delta={abs(ew['start'] - aw['start']):.3f})"
                )
            if abs(ew["end"] - aw["end"]) > 0.05:
                diffs.append(
                    f"word[{i}] end: {ew['end']} != {aw['end']} "
                    f"(delta={abs(ew['end'] - aw['end']):.3f})"
                )

    return diffs


def assert_outputs_equivalent(
    expected: dict,
    actual: dict,
    *,
    label: str = "",
) -> None:
    """Assert two normalized outputs are equivalent.

    Raises AssertionError with a detailed diff if not.

    Args:
        expected: Normalized expected output
        actual: Normalized actual output
        label: Optional label for the assertion message
    """
    diffs = diff_outputs(expected, actual)
    if diffs:
        prefix = f"[{label}] " if label else ""
        diff_str = "\n  ".join(diffs)
        raise AssertionError(f"{prefix}Outputs differ:\n  {diff_str}")
