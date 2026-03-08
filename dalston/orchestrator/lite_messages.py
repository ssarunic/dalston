"""Centralized user-facing strings for lite mode orchestrator.

Usage:

    from dalston.orchestrator.lite_messages import LiteMsg

    raise LiteUnsupportedFeatureError(
        feature="speaker_detection",
        profile=profile,
        remediation=LiteMsg.REMEDIATION_USE_SPEAKER_PROFILE,
    )

All user-facing lite mode strings live here. Keep them alphabetized within
each section for easy scanning.
"""


class LiteMsg:
    """Lite mode message constants.

    Organized by category, then alphabetically.
    """

    # -------------------------------------------------------------------------
    # Profile descriptions
    # -------------------------------------------------------------------------
    PROFILE_DESC_COMPLIANCE = (
        "Transcription with PII detection: "
        "prepare \u2192 transcribe \u2192 pii_detect \u2192 merge. "
        "Requires presidio_analyzer and presidio_anonymizer."
    )
    PROFILE_DESC_CORE = (
        "Minimal transcription pipeline: prepare \u2192 transcribe \u2192 merge. "
        "Zero-config default (M56/M57 baseline)."
    )
    PROFILE_DESC_SPEAKER = (
        "Transcription with speaker diarisation: "
        "prepare \u2192 transcribe \u2192 diarize \u2192 merge."
    )

    # -------------------------------------------------------------------------
    # Exception messages (templates)
    # -------------------------------------------------------------------------
    FEATURE_NOT_SUPPORTED = (
        "Feature '{feature}' is not supported in lite profile "
        "'{profile}'. {remediation}"
    )
    PREREQUISITE_MISSING = (
        "Lite profile '{profile}' requires packages that are not "
        "installed: {packages}. "
        "Install them with: pip install {install_args}"
    )
    PREREQUISITE_REMEDIATION = "Install missing packages: pip install {install_args}"
    PROFILE_NOT_FOUND = (
        "Unknown lite profile '{profile_name}'. Valid profiles: {valid}"
    )

    # -------------------------------------------------------------------------
    # Error codes (machine-readable keys in to_dict())
    # -------------------------------------------------------------------------
    ERR_CODE_PREREQUISITE_MISSING = "lite_prerequisite_missing"
    ERR_CODE_PROFILE_NOT_FOUND = "lite_profile_not_found"
    ERR_CODE_UNSUPPORTED_FEATURE = "lite_unsupported_feature"

    # -------------------------------------------------------------------------
    # Remediation hints
    # -------------------------------------------------------------------------
    REMEDIATION_PII_AUDIO_REDACTION = (
        "Use --profile compliance to enable PII audio redaction "
        "in lite mode."
    )
    REMEDIATION_PII_DETECTION = (
        "Use --profile compliance to enable PII detection in lite mode, "
        "or switch to distributed mode for full PII support."
    )
    REMEDIATION_PII_ENTITY_FILTERING = (
        "Use --profile compliance to enable PII entity filtering in lite mode."
    )
    REMEDIATION_PII_REDACTION_MODE = (
        "Use --profile compliance to enable PII redaction mode in lite mode."
    )
    REMEDIATION_PER_CHANNEL_NOT_SUPPORTED = (
        "per_channel speaker detection is not supported in lite mode. "
        "Use speaker_detection=diarize with the speaker profile, "
        "or switch to distributed mode."
    )
    REMEDIATION_USE_SPEAKER_PROFILE = (
        "Use --profile speaker to enable speaker detection in lite mode, "
        "or switch to distributed mode for full diarisation support."
    )

    # -------------------------------------------------------------------------
    # Runtime errors
    # -------------------------------------------------------------------------
    LITE_MODE_REQUIRED = "Lite pipeline is only available in DALSTON_MODE=lite"
    LITE_MAIN_MODE_REQUIRED = "lite_main can only run in DALSTON_MODE=lite"
