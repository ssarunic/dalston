"""Shared constants for the Dalston application.

This module contains well-known identifiers, magic values, and other
constants that are referenced across multiple modules.
"""

from uuid import UUID

# System retention policy IDs (well-known UUIDs from migration)
# These are created by the database seed migration and should never change.
SYSTEM_POLICY_DEFAULT = UUID("00000000-0000-0000-0000-000000000001")
SYSTEM_POLICY_ZERO_RETENTION = UUID("00000000-0000-0000-0000-000000000002")
SYSTEM_POLICY_KEEP = UUID("00000000-0000-0000-0000-000000000003")
