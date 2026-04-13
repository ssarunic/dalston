"""Common utility functions."""

from collections.abc import Iterable
from datetime import UTC, datetime
from urllib.parse import unquote, urlparse
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


def compute_interval_union_ms(
    intervals: Iterable[tuple[datetime | None, datetime | None]],
) -> int | None:
    """Compute the total covered duration (in ms) across a set of intervals.

    Overlapping intervals are merged so periods that occurred in parallel are
    counted once rather than summed. Used for job-level wait time where tasks
    on different engine instances may have queued concurrently — summing their
    individual waits would overcount the real queue delay experienced by the
    job.

    Args:
        intervals: Iterable of (start, end) timestamp pairs. Pairs with a
            missing endpoint or with end <= start are ignored.

    Returns:
        Union duration in milliseconds, or None if no valid intervals.
    """
    pairs: list[tuple[datetime, datetime]] = []
    for start, end in intervals:
        if start is None or end is None:
            continue
        if end <= start:
            continue
        pairs.append((start, end))

    if not pairs:
        return None

    pairs.sort(key=lambda p: p[0])
    total_seconds = 0.0
    cur_start, cur_end = pairs[0]
    for start, end in pairs[1:]:
        if start <= cur_end:
            if end > cur_end:
                cur_end = end
        else:
            total_seconds += (cur_end - cur_start).total_seconds()
            cur_start, cur_end = start, end
    total_seconds += (cur_end - cur_start).total_seconds()

    return int(total_seconds * 1000)


MAX_DISPLAY_NAME_LENGTH = 255


def generate_display_name(
    filename: str | None = None,
    url: str | None = None,
) -> str:
    """Generate a human-readable display name for a job.

    Priority:
    1. Original filename (from file upload)
    2. Last path segment of URL (without query parameters)
    3. "Untitled — <date>" fallback

    Args:
        filename: Original filename from file upload
        url: Source URL for the audio

    Returns:
        A display name string, truncated to MAX_DISPLAY_NAME_LENGTH
    """
    name: str | None = None

    if filename:
        name = filename.strip()

    if not name and url:
        parsed = urlparse(url)
        # Get last non-empty path segment
        path = unquote(parsed.path).rstrip("/")
        if path:
            segment = path.rsplit("/", 1)[-1].strip()
            if segment:
                name = segment

    if not name:
        now = datetime.now(UTC)
        name = now.strftime("Untitled \u2014 %b %d, %Y %H:%M")

    return name[:MAX_DISPLAY_NAME_LENGTH]


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
