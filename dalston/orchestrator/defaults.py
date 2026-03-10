"""Orchestrator default configuration values.

Centralizes configuration constants for task scheduling and pipeline execution.
"""

# =============================================================================
# Task Retry Configuration
# =============================================================================

DEFAULT_TASK_MAX_RETRIES = 2  # max retry attempts before task fails permanently

# =============================================================================
# PII Detection Defaults
# =============================================================================

DEFAULT_PII_CONFIDENCE_THRESHOLD = 0.5  # minimum confidence to flag as PII
DEFAULT_PII_BUFFER_MS = 50  # audio buffer around PII for redaction (milliseconds)

# =============================================================================
# PII Processing Mode (M67)
# =============================================================================

VALID_PII_MODES = {"pipeline", "post_process"}
DEFAULT_PII_MODE = "pipeline"

# Post-processing retry limits
POST_PROCESSOR_MAX_RETRIES = 2
POST_PROCESSOR_RETRY_DELAY_SECONDS = 5
