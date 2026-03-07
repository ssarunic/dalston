"""Lite capability matrix — single source of truth for M58.

All lite profile enforcement, CLI output, API responses, and docs derive from
this module. Never duplicate profile data elsewhere.

Profile selection precedence (highest to lowest):
    1. Explicit ``profile`` argument passed to ``resolve_profile()``
    2. ``DALSTON_LITE_PROFILE`` environment variable
    3. Default: ``core``

Profiles
--------
core
    Minimal pipeline: prepare → transcribe → merge.
    This is the M56/M57 zero-config default path and must remain unchanged.

speaker
    Adds speaker diarisation: prepare → transcribe → diarize → merge.
    Required expansion target for M58.

compliance
    Adds PII detection: prepare → transcribe → pii_detect → merge.
    Conditional in M58 — only available when prerequisite packages are
    installed (``presidio_analyzer``, ``presidio_anonymizer``).
"""

from __future__ import annotations

import os
from enum import Enum
from typing import Any

from pydantic import BaseModel

MATRIX_VERSION = "1.0.0"
PROFILE_ENV_VAR = "DALSTON_LITE_PROFILE"
DEFAULT_PROFILE = "core"

# Prerequisite Python packages required by each profile.
# Checked via importlib at runtime; absent packages disable the profile.
_COMPLIANCE_PREREQS = ("presidio_analyzer", "presidio_anonymizer")


class LiteProfile(str, Enum):
    CORE = "core"
    SPEAKER = "speaker"
    COMPLIANCE = "compliance"


class ProfileCapability(BaseModel):
    """Capability specification for a single lite profile."""

    profile: LiteProfile
    version: str
    description: str
    stages: list[str]
    """Ordered stage names in the pipeline for this profile."""
    supported_options: dict[str, bool]
    """Map of option name → whether the option is supported in this profile."""
    requires_prereqs: list[str]
    """External Python packages that must be importable for this profile."""


# ---------------------------------------------------------------------------
# Canonical capability matrix
# ---------------------------------------------------------------------------

CAPABILITY_MATRIX: dict[LiteProfile, ProfileCapability] = {
    LiteProfile.CORE: ProfileCapability(
        profile=LiteProfile.CORE,
        version=MATRIX_VERSION,
        description=(
            "Minimal transcription pipeline: prepare → transcribe → merge. "
            "Zero-config default (M56/M57 baseline)."
        ),
        stages=["prepare", "transcribe", "merge"],
        supported_options={
            "language": True,
            "timestamps_granularity": True,
            "speaker_detection": False,
            "num_speakers": False,
            "min_speakers": False,
            "max_speakers": False,
            "pii_detection": False,
            "pii_entity_types": False,
            "redact_pii_audio": False,
            "pii_redaction_mode": False,
        },
        requires_prereqs=[],
    ),
    LiteProfile.SPEAKER: ProfileCapability(
        profile=LiteProfile.SPEAKER,
        version=MATRIX_VERSION,
        description=(
            "Transcription with speaker diarisation: "
            "prepare → transcribe → diarize → merge."
        ),
        stages=["prepare", "transcribe", "diarize", "merge"],
        supported_options={
            "language": True,
            "timestamps_granularity": True,
            "speaker_detection": True,
            "num_speakers": True,
            "min_speakers": True,
            "max_speakers": True,
            "pii_detection": False,
            "pii_entity_types": False,
            "redact_pii_audio": False,
            "pii_redaction_mode": False,
        },
        requires_prereqs=[],
    ),
    LiteProfile.COMPLIANCE: ProfileCapability(
        profile=LiteProfile.COMPLIANCE,
        version=MATRIX_VERSION,
        description=(
            "Transcription with PII detection: "
            "prepare → transcribe → pii_detect → merge. "
            "Requires presidio_analyzer and presidio_anonymizer."
        ),
        stages=["prepare", "transcribe", "pii_detect", "merge"],
        supported_options={
            "language": True,
            "timestamps_granularity": True,
            "speaker_detection": False,
            "num_speakers": False,
            "min_speakers": False,
            "max_speakers": False,
            "pii_detection": True,
            "pii_entity_types": True,
            "redact_pii_audio": True,
            "pii_redaction_mode": True,
        },
        requires_prereqs=list(_COMPLIANCE_PREREQS),
    ),
}


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class LiteUnsupportedFeatureError(Exception):
    """Raised when a requested feature is not available in the active lite profile.

    Always includes a remediation hint pointing the user toward a solution.
    """

    def __init__(
        self,
        feature: str,
        profile: LiteProfile,
        remediation: str,
    ) -> None:
        super().__init__(
            f"Feature '{feature}' is not supported in lite profile "
            f"'{profile.value}'. {remediation}"
        )
        self.feature = feature
        self.profile = profile
        self.remediation = remediation

    def to_dict(self) -> dict[str, Any]:
        return {
            "error": "lite_unsupported_feature",
            "feature": self.feature,
            "profile": self.profile.value,
            "remediation": self.remediation,
            "upgrade_profiles": _upgrade_profiles(self.profile, self.feature),
        }


class LiteProfileNotFoundError(Exception):
    """Raised when an unknown profile name is requested."""

    def __init__(self, profile_name: str) -> None:
        valid = ", ".join(p.value for p in LiteProfile)
        super().__init__(
            f"Unknown lite profile '{profile_name}'. Valid profiles: {valid}"
        )
        self.profile_name = profile_name

    def to_dict(self) -> dict[str, Any]:
        return {
            "error": "lite_profile_not_found",
            "profile": self.profile_name,
            "valid_profiles": [p.value for p in LiteProfile],
        }


class LitePrerequisiteMissingError(Exception):
    """Raised when a lite profile's prerequisite packages are not installed."""

    def __init__(self, profile: LiteProfile, missing: list[str]) -> None:
        packages = ", ".join(missing)
        super().__init__(
            f"Lite profile '{profile.value}' requires packages that are not "
            f"installed: {packages}. "
            f"Install them with: pip install {' '.join(missing)}"
        )
        self.profile = profile
        self.missing = missing

    def to_dict(self) -> dict[str, Any]:
        return {
            "error": "lite_prerequisite_missing",
            "profile": self.profile.value,
            "missing_packages": self.missing,
            "remediation": (
                f"Install missing packages: pip install {' '.join(self.missing)}"
            ),
        }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def resolve_profile(name: str) -> ProfileCapability:
    """Resolve a profile name to its capability specification.

    Args:
        name: Profile name (e.g., "core", "speaker", "compliance").

    Returns:
        ``ProfileCapability`` for the named profile.

    Raises:
        LiteProfileNotFoundError: If the name does not match any known profile.
    """
    try:
        profile = LiteProfile(name.strip().lower())
    except ValueError:
        raise LiteProfileNotFoundError(name) from None
    return CAPABILITY_MATRIX[profile]


def get_active_profile_name() -> str:
    """Return the active profile name from env or default.

    Reads ``DALSTON_LITE_PROFILE`` environment variable.  Returns ``"core"``
    when unset.
    """
    return os.getenv(PROFILE_ENV_VAR, DEFAULT_PROFILE).strip().lower()


def get_active_profile() -> ProfileCapability:
    """Return the active ``ProfileCapability`` for the current environment."""
    return resolve_profile(get_active_profile_name())


def validate_request(profile: LiteProfile, parameters: dict) -> None:
    """Validate job parameters against the lite capability matrix.

    Checked options: ``speaker_detection``, ``pii_detection``,
    ``redact_pii_audio``.  Unknown options are ignored (forward-compat).

    Args:
        profile: The active lite profile.
        parameters: Job parameters from the API/CLI request.

    Raises:
        LiteUnsupportedFeatureError: On first unsupported option encountered.
    """
    caps = CAPABILITY_MATRIX[profile]

    speaker_detection = parameters.get("speaker_detection", "none") or "none"
    if speaker_detection not in ("none", ""):
        if not caps.supported_options.get("speaker_detection", False):
            raise LiteUnsupportedFeatureError(
                feature="speaker_detection",
                profile=profile,
                remediation=(
                    "Use --profile speaker to enable speaker detection in lite mode, "
                    "or switch to distributed mode for full diarisation support."
                ),
            )
        if speaker_detection == "per_channel":
            raise LiteUnsupportedFeatureError(
                feature="speaker_detection=per_channel",
                profile=profile,
                remediation=(
                    "Per-channel speaker detection is not supported in lite mode. "
                    "Use speaker_detection=diarize with the speaker profile, "
                    "or switch to distributed mode."
                ),
            )

    if parameters.get("pii_detection", False):
        if not caps.supported_options.get("pii_detection", False):
            raise LiteUnsupportedFeatureError(
                feature="pii_detection",
                profile=profile,
                remediation=(
                    "Use --profile compliance to enable PII detection in lite mode, "
                    "or switch to distributed mode for full PII support."
                ),
            )

    if parameters.get("redact_pii_audio", False):
        if not caps.supported_options.get("redact_pii_audio", False):
            raise LiteUnsupportedFeatureError(
                feature="redact_pii_audio",
                profile=profile,
                remediation=(
                    "Use --profile compliance to enable PII audio redaction "
                    "in lite mode."
                ),
            )


def check_prerequisites(profile: LiteProfile) -> list[str]:
    """Return a list of missing prerequisite package names for *profile*.

    An empty list means all prerequisites are satisfied.
    """
    import importlib

    caps = CAPABILITY_MATRIX[profile]
    missing = []
    for prereq in caps.requires_prereqs:
        try:
            importlib.import_module(prereq)
        except ImportError:
            missing.append(prereq)
    return missing


def get_matrix_as_dict() -> dict[str, Any]:
    """Serialise the full capability matrix to a plain dict.

    Used by the capability discovery endpoint and docs generation to ensure
    they derive from the single source of truth rather than duplicating data.
    """
    return {
        "schema_version": MATRIX_VERSION,
        "default_profile": DEFAULT_PROFILE,
        "profile_precedence": [
            "explicit_argument",
            f"env:{PROFILE_ENV_VAR}",
            f"default:{DEFAULT_PROFILE}",
        ],
        "profiles": {
            profile.value: cap.model_dump(mode="json")
            for profile, cap in CAPABILITY_MATRIX.items()
        },
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _upgrade_profiles(current: LiteProfile, feature: str) -> list[str]:
    """Return profiles that do support *feature*."""
    return [
        p.value
        for p, cap in CAPABILITY_MATRIX.items()
        if cap.supported_options.get(feature, False) and p != current
    ]
