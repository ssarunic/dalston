"""Common utility functions."""

from datetime import datetime


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
