"""Smoke tests for centralized string constant modules.

Validates that:
1. All .format()-style templates can be called without KeyError/IndexError.
2. Err.structured() raises on unknown codes.
3. All _STRUCTURED entries reference valid Err attributes.
"""

from __future__ import annotations

import re

import pytest

from dalston.gateway.error_codes import Err
from dalston.orchestrator.lite_messages import LiteMsg

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Matches {name} and {name:spec} placeholders in str.format() templates.
_FORMAT_FIELD_RE = re.compile(r"\{(\w+)(?::[^}]*)?\}")


def _extract_format_fields(template: str) -> set[str]:
    """Return the set of field names used in a str.format() template."""
    return set(_FORMAT_FIELD_RE.findall(template))


def _string_class_attrs(cls: type) -> list[tuple[str, str]]:
    """Return (attr_name, value) for all public string class attributes."""
    result = []
    for name in sorted(dir(cls)):
        if name.startswith("_"):
            continue
        value = getattr(cls, name)
        if isinstance(value, str) and not callable(value):
            result.append((name, value))
    return result


def _dummy_value(field: str) -> object:
    """Return a plausible dummy value for a format field name."""
    # size_mb uses :.1f so needs a float
    if "mb" in field or "size" in field:
        return 1.0
    if "limit" in field or "length" in field or "channels" in field:
        return 1
    return "test"


# ---------------------------------------------------------------------------
# Err smoke tests
# ---------------------------------------------------------------------------


class TestErrFormatPlaceholders:
    """Every Err constant with {placeholders} must format without error."""

    @pytest.mark.parametrize(
        "attr_name,template",
        _string_class_attrs(Err),
        ids=[name for name, _ in _string_class_attrs(Err)],
    )
    def test_format_succeeds(self, attr_name: str, template: str) -> None:
        fields = _extract_format_fields(template)
        if not fields:
            return  # No placeholders — nothing to test.

        kwargs = {f: _dummy_value(f) for f in fields}
        result = template.format(**kwargs)
        assert isinstance(result, str)
        assert len(result) > 0


class TestErrStructured:
    """Err.structured() contract tests."""

    def test_known_codes_return_dict(self) -> None:
        for code in Err._STRUCTURED:
            result = Err.structured(code)
            assert result["code"] == code
            assert isinstance(result["message"], str)
            assert len(result["message"]) > 0

    def test_unknown_code_raises_key_error(self) -> None:
        with pytest.raises(KeyError, match="Unknown structured error code"):
            Err.structured("totally_bogus_code")

    def test_message_override_bypasses_lookup(self) -> None:
        result = Err.structured("any_code", message="Custom message")
        assert result["message"] == "Custom message"
        assert result["code"] == "any_code"

    def test_extra_fields_included(self) -> None:
        result = Err.structured("job_not_found", purged_at="2024-01-01T00:00:00Z")
        assert result["purged_at"] == "2024-01-01T00:00:00Z"

    def test_structured_values_match_class_attrs(self) -> None:
        """All _STRUCTURED values that reference Err attributes must match."""
        for code, msg in Err._STRUCTURED.items():
            # Skip inline strings (like "Task has not started yet")
            # Verify that referenced constants resolve to the same value.
            matching_attrs = [
                name
                for name in dir(Err)
                if not name.startswith("_")
                and isinstance(getattr(Err, name), str)
                and getattr(Err, name) == msg
            ]
            # Either it's a direct reference (has matching attr) or an inline string
            assert len(matching_attrs) > 0 or msg == "Task has not started yet", (
                f"_STRUCTURED[{code!r}] = {msg!r} doesn't match any Err constant"
            )


# ---------------------------------------------------------------------------
# LiteMsg smoke tests
# ---------------------------------------------------------------------------


class TestLiteMsgFormatPlaceholders:
    """Every LiteMsg constant with {placeholders} must format without error."""

    @pytest.mark.parametrize(
        "attr_name,template",
        _string_class_attrs(LiteMsg),
        ids=[name for name, _ in _string_class_attrs(LiteMsg)],
    )
    def test_format_succeeds(self, attr_name: str, template: str) -> None:
        fields = _extract_format_fields(template)
        if not fields:
            return

        kwargs = {f: _dummy_value(f) for f in fields}
        result = template.format(**kwargs)
        assert isinstance(result, str)
        assert len(result) > 0


# ---------------------------------------------------------------------------
# CLIMsg smoke tests (import guarded — module may not be on sys.path in all
# test environments)
# ---------------------------------------------------------------------------


class TestCLIMsgFormatPlaceholders:
    """Every CLIMsg constant with {placeholders} must format without error."""

    @pytest.fixture(autouse=True)
    def _import_cli_msg(self) -> None:
        try:
            from dalston_cli.messages import CLIMsg

            self._cls = CLIMsg
        except ImportError:
            pytest.skip("dalston_cli not installed")

    @pytest.mark.parametrize(
        "attr_name",
        [
            "BOOTSTRAP_ENSURING_MODEL",
            "BOOTSTRAP_FAILED",
            "BOOTSTRAP_HOW_TO_FIX",
            "ERR_INVALID_LITE_PROFILE",
            "ERR_PROCESSING",
            "ERR_TRANSCRIPTION_FAILED",
            "MODEL_NOT_READY",
            "MODEL_NOT_READY_REMEDIATION",
            "SUBMITTING_FILE",
            "SUBMITTING_URL",
        ],
    )
    def test_format_succeeds(self, attr_name: str) -> None:
        template = getattr(self._cls, attr_name)
        fields = _extract_format_fields(template)
        if not fields:
            return

        kwargs = {f: _dummy_value(f) for f in fields}
        result = template.format(**kwargs)
        assert isinstance(result, str)
        assert len(result) > 0
