"""Common utility functions."""

from datetime import datetime
from uuid import UUID


def compute_duration_ms(
    started_at: datetime | None, completed_at: datetime | None
) -> int | None:
    """Compute duration in milliseconds from timestamps.

    Args:
        started_at: Start timestamp
        completed_at: End timestamp

    Returns:
        Duration in milliseconds, or None if either timestamp is missing
    """
    if started_at is None or completed_at is None:
        return None
    delta = completed_at - started_at
    return int(delta.total_seconds() * 1000)


def parse_session_id(session_id: str) -> UUID:
    """Parse session ID string to UUID.

    Session IDs can be:
    - sess_<hex> format (e.g., sess_abc123def456...)
    - Raw UUID string

    Args:
        session_id: Session ID string

    Returns:
        UUID

    Raises:
        ValueError: If session_id is invalid
    """
    if session_id.startswith("sess_"):
        # Extract hex part and pad to 32 chars for UUID
        hex_part = session_id[5:]
        if not hex_part:
            raise ValueError(f"Invalid session ID: {session_id}")
        # Pad to 32 chars (UUID requires 32 hex digits)
        padded = hex_part.ljust(32, "0")
        return UUID(padded)
    else:
        return UUID(session_id)
