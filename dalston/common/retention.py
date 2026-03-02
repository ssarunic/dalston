"""Retention policy constants.

Centralizes retention sentinel values and limits used across the codebase
for data lifecycle management.
"""

# =============================================================================
# Retention Sentinel Values
# =============================================================================
# These special values control retention behavior:
# - TRANSIENT (0): Data is not stored / immediate purge after completion
# - PERMANENT (-1): Data is kept forever (purge_after stays NULL)
# - Positive integers: Number of days to retain before auto-deletion

RETENTION_TRANSIENT = 0  # Do not store / immediate purge
RETENTION_PERMANENT = -1  # Keep forever

# =============================================================================
# Retention Limits
# =============================================================================

RETENTION_MIN_DAYS = 1  # Minimum retention when using days (not transient/permanent)
RETENTION_MAX_DAYS = 3650  # ~10 years maximum retention

# =============================================================================
# Defaults
# =============================================================================

RETENTION_DEFAULT_DAYS = 30  # Default retention for jobs and sessions

# =============================================================================
# Time Conversion
# =============================================================================

SECONDS_PER_DAY = 86400  # Used for retention_days -> TTL conversion
