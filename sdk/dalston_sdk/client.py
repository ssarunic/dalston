"""Batch transcription client for Dalston.

Provides both synchronous (Dalston) and asynchronous (AsyncDalston) clients
for interacting with the Dalston transcription API.
"""

from __future__ import annotations

import time
import warnings
from pathlib import Path
from typing import Any, BinaryIO, Callable
from urllib.parse import urlparse
from uuid import UUID

import httpx

from .exceptions import (
    AuthenticationError,
    ConnectError,
    DalstonError,
    ForbiddenError,
    NotFoundError,
    RateLimitError,
    ServerError,
    TimeoutException,
    ValidationError,
)
from .types import (
    ExportFormat,
    HealthStatus,
    Job,
    JobList,
    JobStatus,
    JobSummary,
    RealtimeStatus,
    Segment,
    SessionToken,
    Speaker,
    SpeakerDetection,
    TimestampGranularity,
    Transcript,
    Word,
)


def _parse_job(data: dict[str, Any]) -> Job:
    """Parse job response JSON into Job object."""
    transcript = None
    if data.get("text") is not None:
        words = None
        if data.get("words"):
            words = [
                Word(
                    text=w.get("word") or w.get("text", ""),
                    start=w["start"],
                    end=w["end"],
                    confidence=w.get("confidence") or w.get("probability"),
                    speaker_id=w.get("speaker_id"),
                )
                for w in data["words"]
            ]

        segments = None
        if data.get("segments"):
            segments = [
                Segment(
                    id=s.get("id", i),
                    text=s["text"],
                    start=s["start"],
                    end=s["end"],
                    speaker_id=s.get("speaker_id"),
                    words=[
                        Word(
                            text=w.get("word") or w.get("text", ""),
                            start=w["start"],
                            end=w["end"],
                            confidence=w.get("confidence") or w.get("probability"),
                            speaker_id=w.get("speaker_id"),
                        )
                        for w in s.get("words", [])
                    ]
                    if s.get("words")
                    else None,
                )
                for i, s in enumerate(data["segments"])
            ]

        speakers = None
        if data.get("speakers"):
            speakers = [
                Speaker(
                    id=sp["id"],
                    label=sp.get("label"),
                    total_duration=sp.get("total_duration"),
                )
                for sp in data["speakers"]
            ]

        transcript = Transcript(
            text=data["text"],
            language_code=data.get("language_code"),
            words=words,
            segments=segments,
            speakers=speakers,
        )

    return Job(
        id=UUID(data["id"]) if isinstance(data["id"], str) else data["id"],
        status=JobStatus(data["status"]),
        created_at=_parse_datetime(data["created_at"]),
        started_at=_parse_datetime(data.get("started_at")),
        completed_at=_parse_datetime(data.get("completed_at")),
        error=data.get("error"),
        progress=data.get("progress"),
        current_stage=data.get("current_stage"),
        transcript=transcript,
    )


def _parse_datetime(value: str | None) -> "datetime | None":
    """Parse ISO datetime string.

    Args:
        value: ISO 8601 datetime string or None.

    Returns:
        Parsed datetime object, or None if value is None or unparseable.
    """
    if value is None:
        return None
    from datetime import datetime

    # Handle various ISO formats
    value = value.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        # Return None for unparseable values to maintain type safety
        return None


def _handle_error(response: httpx.Response) -> None:
    """Raise appropriate exception for error responses."""
    status = response.status_code

    try:
        detail = response.json().get("detail", response.text)
    except Exception:
        detail = response.text

    if status == 401:
        raise AuthenticationError(str(detail))
    elif status == 403:
        raise ForbiddenError(str(detail))
    elif status == 404:
        raise NotFoundError(str(detail))
    elif status == 429:
        retry_after = response.headers.get("Retry-After")
        raise RateLimitError(
            str(detail),
            retry_after=int(retry_after) if retry_after else None,
        )
    elif status == 400 or status == 422:
        raise ValidationError(str(detail), status_code=status)
    elif status >= 500:
        raise ServerError(str(detail))
    else:
        raise DalstonError(str(detail), status_code=status)


class Dalston:
    """Synchronous client for Dalston batch transcription API.

    Example:
        ```python
        client = Dalston(base_url="http://localhost:8000", api_key="your-key")

        # Submit audio for transcription
        job = client.transcribe("audio.mp3", language="en")

        # Wait for completion
        job = client.wait_for_completion(job.id)

        # Access results
        print(job.transcript.text)
        ```
    """

    def __init__(
        self,
        base_url: str = "http://localhost:8000",
        api_key: str | None = None,
        timeout: float = 120.0,
    ) -> None:
        """Initialize the Dalston client.

        Args:
            base_url: Base URL of the Dalston server.
            api_key: Optional API key for authentication.
            timeout: Request timeout in seconds.
        """
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self._client = httpx.Client(timeout=timeout)

        # Warn if API key is sent over unencrypted HTTP to non-localhost
        if api_key:
            parsed = urlparse(self.base_url)
            if parsed.scheme == "http" and parsed.hostname not in (
                "localhost",
                "127.0.0.1",
                "::1",
            ):
                warnings.warn(
                    f"API key is being sent over unencrypted HTTP to {parsed.hostname}. "
                    "Consider using HTTPS to protect your credentials.",
                    UserWarning,
                    stacklevel=2,
                )

    def _headers(self) -> dict[str, str]:
        """Build request headers."""
        headers: dict[str, str] = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def close(self) -> None:
        """Close the HTTP client."""
        self._client.close()

    def __enter__(self) -> "Dalston":
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def transcribe(
        self,
        file: str | Path | BinaryIO | None = None,
        audio_url: str | None = None,
        language: str = "auto",
        speaker_detection: SpeakerDetection | str = SpeakerDetection.NONE,
        num_speakers: int | None = None,
        timestamps_granularity: TimestampGranularity | str = TimestampGranularity.WORD,
        webhook_url: str | None = None,
        webhook_metadata: dict[str, Any] | None = None,
    ) -> Job:
        """Submit audio for transcription.

        Args:
            file: Path to audio file, or file-like object.
            audio_url: URL to fetch audio from (alternative to file).
            language: Language code or "auto" for detection.
            speaker_detection: Speaker detection mode.
            num_speakers: Expected number of speakers (for diarization).
            timestamps_granularity: Level of timestamp detail.
            webhook_url: URL for completion callback.
            webhook_metadata: Custom data to include in webhook.

        Returns:
            Job object with ID and initial status.

        Raises:
            ValidationError: If neither file nor audio_url provided.
            DalstonError: On API errors.
        """
        if file is None and audio_url is None:
            raise ValidationError("Either file or audio_url must be provided")

        # Build form data
        data: dict[str, Any] = {
            "language": language,
            "speaker_detection": (
                speaker_detection.value
                if isinstance(speaker_detection, SpeakerDetection)
                else speaker_detection
            ),
            "timestamps_granularity": (
                timestamps_granularity.value
                if isinstance(timestamps_granularity, TimestampGranularity)
                else timestamps_granularity
            ),
        }

        if num_speakers is not None:
            data["num_speakers"] = num_speakers
        if webhook_url is not None:
            data["webhook_url"] = webhook_url
        if webhook_metadata is not None:
            import json

            data["webhook_metadata"] = json.dumps(webhook_metadata)

        # Handle file upload
        files: dict[str, Any] | None = None
        opened_file = None
        try:
            if file is not None:
                if isinstance(file, (str, Path)):
                    path = Path(file)
                    opened_file = open(path, "rb")
                    files = {"file": (path.name, opened_file)}
                else:
                    # File-like object - extract basename for cross-platform safety
                    filename = getattr(file, "name", "audio")
                    if isinstance(filename, str):
                        filename = Path(filename).name
                    files = {"file": (filename, file)}

            response = self._client.post(
                f"{self.base_url}/v1/audio/transcriptions",
                data=data,
                files=files,
                headers=self._headers(),
            )
        except httpx.ConnectError as e:
            raise ConnectError(f"Failed to connect: {e}") from e
        except httpx.TimeoutException as e:
            raise TimeoutException(f"Request timed out: {e}") from e
        finally:
            # Close file if we opened it
            if opened_file is not None:
                opened_file.close()

        if response.status_code != 201:
            _handle_error(response)

        return _parse_job(response.json())

    def get_job(self, job_id: UUID | str) -> Job:
        """Get job status and results.

        Args:
            job_id: Job ID to retrieve.

        Returns:
            Job object with current status and transcript if completed.

        Raises:
            NotFoundError: If job doesn't exist.
        """
        try:
            response = self._client.get(
                f"{self.base_url}/v1/audio/transcriptions/{job_id}",
                headers=self._headers(),
            )
        except httpx.ConnectError as e:
            raise ConnectError(f"Failed to connect: {e}") from e
        except httpx.TimeoutException as e:
            raise TimeoutException(f"Request timed out: {e}") from e

        if response.status_code != 200:
            _handle_error(response)

        return _parse_job(response.json())

    def list_jobs(
        self,
        limit: int = 20,
        offset: int = 0,
        status: JobStatus | str | None = None,
    ) -> JobList:
        """List transcription jobs.

        Args:
            limit: Maximum number of jobs to return (1-100).
            offset: Pagination offset.
            status: Filter by job status.

        Returns:
            JobList with jobs and pagination info.
        """
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if status is not None:
            params["status"] = status.value if isinstance(status, JobStatus) else status

        try:
            response = self._client.get(
                f"{self.base_url}/v1/audio/transcriptions",
                params=params,
                headers=self._headers(),
            )
        except httpx.ConnectError as e:
            raise ConnectError(f"Failed to connect: {e}") from e
        except httpx.TimeoutException as e:
            raise TimeoutException(f"Request timed out: {e}") from e

        if response.status_code != 200:
            _handle_error(response)

        data = response.json()
        return JobList(
            jobs=[
                JobSummary(
                    id=UUID(j["id"]) if isinstance(j["id"], str) else j["id"],
                    status=JobStatus(j["status"]),
                    created_at=_parse_datetime(j["created_at"]),
                    started_at=_parse_datetime(j.get("started_at")),
                    completed_at=_parse_datetime(j.get("completed_at")),
                    progress=j.get("progress"),
                )
                for j in data["jobs"]
            ],
            total=data["total"],
            limit=data["limit"],
            offset=data["offset"],
        )

    def wait_for_completion(
        self,
        job_id: UUID | str,
        poll_interval: float = 1.0,
        timeout: float | None = None,
        on_progress: Callable[[int, str | None], None] | None = None,
    ) -> Job:
        """Wait for job to complete.

        Args:
            job_id: Job ID to wait for.
            poll_interval: Seconds between status checks.
            timeout: Maximum time to wait (None for unlimited).
            on_progress: Callback for progress updates (progress, stage).

        Returns:
            Completed job with transcript.

        Raises:
            TimeoutError: If timeout exceeded.
            DalstonError: If job fails.
        """
        start_time = time.monotonic()

        while True:
            job = self.get_job(job_id)

            if job.status == JobStatus.COMPLETED:
                return job
            elif job.status == JobStatus.FAILED:
                raise DalstonError(
                    f"Job failed: {job.error or 'Unknown error'}",
                    status_code=None,
                )
            elif job.status == JobStatus.CANCELLED:
                raise DalstonError("Job was cancelled", status_code=None)

            # Call progress callback
            if on_progress and job.progress is not None:
                on_progress(job.progress, job.current_stage)

            # Check timeout
            if timeout is not None:
                elapsed = time.monotonic() - start_time
                if elapsed >= timeout:
                    raise TimeoutException(
                        f"Timeout waiting for job {job_id} after {elapsed:.1f}s"
                    )

            time.sleep(poll_interval)

    def export(
        self,
        job_id: UUID | str,
        format: ExportFormat | str = ExportFormat.JSON,
        include_speakers: bool = True,
        max_line_length: int = 42,
        max_lines: int = 2,
    ) -> str | dict[str, Any]:
        """Export transcript in specified format.

        Args:
            job_id: Job ID to export.
            format: Export format (srt, vtt, txt, json).
            include_speakers: Include speaker labels in output.
            max_line_length: Max characters per subtitle line.
            max_lines: Max lines per subtitle block.

        Returns:
            Exported transcript as string (srt/vtt/txt) or dict (json).

        Raises:
            NotFoundError: If job doesn't exist.
            ValidationError: If job not completed or invalid format.
        """
        format_str = format.value if isinstance(format, ExportFormat) else format

        params = {
            "include_speakers": include_speakers,
            "max_line_length": max_line_length,
            "max_lines": max_lines,
        }

        try:
            response = self._client.get(
                f"{self.base_url}/v1/audio/transcriptions/{job_id}/export/{format_str}",
                params=params,
                headers=self._headers(),
            )
        except httpx.ConnectError as e:
            raise ConnectError(f"Failed to connect: {e}") from e
        except httpx.TimeoutException as e:
            raise TimeoutException(f"Request timed out: {e}") from e

        if response.status_code != 200:
            _handle_error(response)

        if format_str == "json":
            return response.json()
        return response.text

    def health(self) -> HealthStatus:
        """Check server health.

        Returns:
            HealthStatus with server status.

        Raises:
            ConnectionError: If server is unreachable.
        """
        try:
            response = self._client.get(
                f"{self.base_url}/health",
                headers=self._headers(),
            )
        except httpx.ConnectError as e:
            raise ConnectError(f"Failed to connect: {e}") from e
        except httpx.TimeoutException as e:
            raise TimeoutException(f"Request timed out: {e}") from e

        if response.status_code != 200:
            _handle_error(response)

        data = response.json()
        return HealthStatus(status=data.get("status", "unknown"))

    def get_realtime_status(self) -> RealtimeStatus:
        """Get real-time transcription system status.

        Returns:
            RealtimeStatus with capacity and availability info.

        Raises:
            ConnectionError: If server is unreachable.
        """
        try:
            response = self._client.get(
                f"{self.base_url}/v1/realtime/status",
                headers=self._headers(),
            )
        except httpx.ConnectError as e:
            raise ConnectError(f"Failed to connect: {e}") from e
        except httpx.TimeoutException as e:
            raise TimeoutException(f"Request timed out: {e}") from e

        if response.status_code != 200:
            _handle_error(response)

        data = response.json()
        return RealtimeStatus(
            status=data.get("status", "unknown"),
            total_capacity=data.get("total_capacity", 0),
            active_sessions=data.get("active_sessions", 0),
            available_capacity=data.get("available_capacity", 0),
            worker_count=data.get("worker_count", 0),
            ready_workers=data.get("ready_workers", 0),
        )

    def create_session_token(
        self,
        ttl: int = 600,
        scopes: list[str] | None = None,
    ) -> SessionToken:
        """Create an ephemeral session token for client-side WebSocket auth.

        Session tokens are short-lived and designed for browser clients
        that need to connect directly to WebSocket endpoints without
        exposing long-lived API keys.

        Args:
            ttl: Time-to-live in seconds (60-3600). Default 600 (10 minutes).
            scopes: Requested scopes. Defaults to ["realtime"].
                    Cannot exceed the parent API key's scopes.

        Returns:
            SessionToken with the token and expiry info.

        Raises:
            PermissionError: If API key lacks 'realtime' scope.
            ValidationError: If invalid scopes or TTL.
        """
        payload: dict[str, Any] = {"ttl": ttl}
        if scopes is not None:
            payload["scopes"] = scopes

        try:
            response = self._client.post(
                f"{self.base_url}/auth/tokens",
                json=payload,
                headers=self._headers(),
            )
        except httpx.ConnectError as e:
            raise ConnectError(f"Failed to connect: {e}") from e
        except httpx.TimeoutException as e:
            raise TimeoutException(f"Request timed out: {e}") from e

        if response.status_code != 201:
            _handle_error(response)

        data = response.json()
        return SessionToken(
            token=data["token"],
            expires_at=_parse_datetime(data["expires_at"]),
            scopes=data["scopes"],
            tenant_id=UUID(data["tenant_id"]),
        )


class AsyncDalston:
    """Asynchronous client for Dalston batch transcription API.

    Example:
        ```python
        async with AsyncDalston(base_url="http://localhost:8000") as client:
            job = await client.transcribe("audio.mp3")
            job = await client.wait_for_completion(job.id)
            print(job.transcript.text)
        ```
    """

    def __init__(
        self,
        base_url: str = "http://localhost:8000",
        api_key: str | None = None,
        timeout: float = 120.0,
    ) -> None:
        """Initialize the async Dalston client.

        Args:
            base_url: Base URL of the Dalston server.
            api_key: Optional API key for authentication.
            timeout: Request timeout in seconds.
        """
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self._client = httpx.AsyncClient(timeout=timeout)

        # Warn if API key is sent over unencrypted HTTP to non-localhost
        if api_key:
            parsed = urlparse(self.base_url)
            if parsed.scheme == "http" and parsed.hostname not in (
                "localhost",
                "127.0.0.1",
                "::1",
            ):
                warnings.warn(
                    f"API key is being sent over unencrypted HTTP to {parsed.hostname}. "
                    "Consider using HTTPS to protect your credentials.",
                    UserWarning,
                    stacklevel=2,
                )

    def _headers(self) -> dict[str, str]:
        """Build request headers."""
        headers: dict[str, str] = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    async def close(self) -> None:
        """Close the HTTP client."""
        await self._client.aclose()

    async def __aenter__(self) -> "AsyncDalston":
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()

    async def transcribe(
        self,
        file: str | Path | BinaryIO | None = None,
        audio_url: str | None = None,
        language: str = "auto",
        speaker_detection: SpeakerDetection | str = SpeakerDetection.NONE,
        num_speakers: int | None = None,
        timestamps_granularity: TimestampGranularity | str = TimestampGranularity.WORD,
        webhook_url: str | None = None,
        webhook_metadata: dict[str, Any] | None = None,
    ) -> Job:
        """Submit audio for transcription.

        Args:
            file: Path to audio file, or file-like object.
            audio_url: URL to fetch audio from (alternative to file).
            language: Language code or "auto" for detection.
            speaker_detection: Speaker detection mode.
            num_speakers: Expected number of speakers (for diarization).
            timestamps_granularity: Level of timestamp detail.
            webhook_url: URL for completion callback.
            webhook_metadata: Custom data to include in webhook.

        Returns:
            Job object with ID and initial status.
        """
        if file is None and audio_url is None:
            raise ValidationError("Either file or audio_url must be provided")

        # Build form data
        data: dict[str, Any] = {
            "language": language,
            "speaker_detection": (
                speaker_detection.value
                if isinstance(speaker_detection, SpeakerDetection)
                else speaker_detection
            ),
            "timestamps_granularity": (
                timestamps_granularity.value
                if isinstance(timestamps_granularity, TimestampGranularity)
                else timestamps_granularity
            ),
        }

        if num_speakers is not None:
            data["num_speakers"] = num_speakers
        if webhook_url is not None:
            data["webhook_url"] = webhook_url
        if webhook_metadata is not None:
            import json

            data["webhook_metadata"] = json.dumps(webhook_metadata)

        # Handle file upload
        files: dict[str, Any] | None = None
        opened_file = None
        if file is not None:
            if isinstance(file, (str, Path)):
                path = Path(file)
                opened_file = open(path, "rb")
                files = {"file": (path.name, opened_file)}
            else:
                # File-like object - extract basename for cross-platform safety
                filename = getattr(file, "name", "audio")
                if isinstance(filename, str):
                    filename = Path(filename).name
                files = {"file": (filename, file)}

        try:
            response = await self._client.post(
                f"{self.base_url}/v1/audio/transcriptions",
                data=data,
                files=files,
                headers=self._headers(),
            )
        except httpx.ConnectError as e:
            raise ConnectError(f"Failed to connect: {e}") from e
        except httpx.TimeoutException as e:
            raise TimeoutException(f"Request timed out: {e}") from e
        finally:
            if opened_file:
                opened_file.close()

        if response.status_code != 201:
            _handle_error(response)

        return _parse_job(response.json())

    async def get_job(self, job_id: UUID | str) -> Job:
        """Get job status and results.

        Args:
            job_id: Job ID to retrieve.

        Returns:
            Job object with current status and transcript if completed.
        """
        try:
            response = await self._client.get(
                f"{self.base_url}/v1/audio/transcriptions/{job_id}",
                headers=self._headers(),
            )
        except httpx.ConnectError as e:
            raise ConnectError(f"Failed to connect: {e}") from e
        except httpx.TimeoutException as e:
            raise TimeoutException(f"Request timed out: {e}") from e

        if response.status_code != 200:
            _handle_error(response)

        return _parse_job(response.json())

    async def list_jobs(
        self,
        limit: int = 20,
        offset: int = 0,
        status: JobStatus | str | None = None,
    ) -> JobList:
        """List transcription jobs.

        Args:
            limit: Maximum number of jobs to return (1-100).
            offset: Pagination offset.
            status: Filter by job status.

        Returns:
            JobList with jobs and pagination info.
        """
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if status is not None:
            params["status"] = status.value if isinstance(status, JobStatus) else status

        try:
            response = await self._client.get(
                f"{self.base_url}/v1/audio/transcriptions",
                params=params,
                headers=self._headers(),
            )
        except httpx.ConnectError as e:
            raise ConnectError(f"Failed to connect: {e}") from e
        except httpx.TimeoutException as e:
            raise TimeoutException(f"Request timed out: {e}") from e

        if response.status_code != 200:
            _handle_error(response)

        data = response.json()
        return JobList(
            jobs=[
                JobSummary(
                    id=UUID(j["id"]) if isinstance(j["id"], str) else j["id"],
                    status=JobStatus(j["status"]),
                    created_at=_parse_datetime(j["created_at"]),
                    started_at=_parse_datetime(j.get("started_at")),
                    completed_at=_parse_datetime(j.get("completed_at")),
                    progress=j.get("progress"),
                )
                for j in data["jobs"]
            ],
            total=data["total"],
            limit=data["limit"],
            offset=data["offset"],
        )

    async def wait_for_completion(
        self,
        job_id: UUID | str,
        poll_interval: float = 1.0,
        timeout: float | None = None,
        on_progress: Callable[[int, str | None], None] | None = None,
    ) -> Job:
        """Wait for job to complete.

        Args:
            job_id: Job ID to wait for.
            poll_interval: Seconds between status checks.
            timeout: Maximum time to wait (None for unlimited).
            on_progress: Callback for progress updates (progress, stage).

        Returns:
            Completed job with transcript.
        """
        import asyncio

        start_time = time.monotonic()

        while True:
            job = await self.get_job(job_id)

            if job.status == JobStatus.COMPLETED:
                return job
            elif job.status == JobStatus.FAILED:
                raise DalstonError(
                    f"Job failed: {job.error or 'Unknown error'}",
                    status_code=None,
                )
            elif job.status == JobStatus.CANCELLED:
                raise DalstonError("Job was cancelled", status_code=None)

            # Call progress callback
            if on_progress and job.progress is not None:
                on_progress(job.progress, job.current_stage)

            # Check timeout
            if timeout is not None:
                elapsed = time.monotonic() - start_time
                if elapsed >= timeout:
                    raise TimeoutException(
                        f"Timeout waiting for job {job_id} after {elapsed:.1f}s"
                    )

            await asyncio.sleep(poll_interval)

    async def export(
        self,
        job_id: UUID | str,
        format: ExportFormat | str = ExportFormat.JSON,
        include_speakers: bool = True,
        max_line_length: int = 42,
        max_lines: int = 2,
    ) -> str | dict[str, Any]:
        """Export transcript in specified format.

        Args:
            job_id: Job ID to export.
            format: Export format (srt, vtt, txt, json).
            include_speakers: Include speaker labels in output.
            max_line_length: Max characters per subtitle line.
            max_lines: Max lines per subtitle block.

        Returns:
            Exported transcript as string (srt/vtt/txt) or dict (json).
        """
        format_str = format.value if isinstance(format, ExportFormat) else format

        params = {
            "include_speakers": include_speakers,
            "max_line_length": max_line_length,
            "max_lines": max_lines,
        }

        try:
            response = await self._client.get(
                f"{self.base_url}/v1/audio/transcriptions/{job_id}/export/{format_str}",
                params=params,
                headers=self._headers(),
            )
        except httpx.ConnectError as e:
            raise ConnectError(f"Failed to connect: {e}") from e
        except httpx.TimeoutException as e:
            raise TimeoutException(f"Request timed out: {e}") from e

        if response.status_code != 200:
            _handle_error(response)

        if format_str == "json":
            return response.json()
        return response.text

    async def health(self) -> HealthStatus:
        """Check server health.

        Returns:
            HealthStatus with server status.

        Raises:
            ConnectionError: If server is unreachable.
        """
        try:
            response = await self._client.get(
                f"{self.base_url}/health",
                headers=self._headers(),
            )
        except httpx.ConnectError as e:
            raise ConnectError(f"Failed to connect: {e}") from e
        except httpx.TimeoutException as e:
            raise TimeoutException(f"Request timed out: {e}") from e

        if response.status_code != 200:
            _handle_error(response)

        data = response.json()
        return HealthStatus(status=data.get("status", "unknown"))

    async def get_realtime_status(self) -> RealtimeStatus:
        """Get real-time transcription system status.

        Returns:
            RealtimeStatus with capacity and availability info.

        Raises:
            ConnectionError: If server is unreachable.
        """
        try:
            response = await self._client.get(
                f"{self.base_url}/v1/realtime/status",
                headers=self._headers(),
            )
        except httpx.ConnectError as e:
            raise ConnectError(f"Failed to connect: {e}") from e
        except httpx.TimeoutException as e:
            raise TimeoutException(f"Request timed out: {e}") from e

        if response.status_code != 200:
            _handle_error(response)

        data = response.json()
        return RealtimeStatus(
            status=data.get("status", "unknown"),
            total_capacity=data.get("total_capacity", 0),
            active_sessions=data.get("active_sessions", 0),
            available_capacity=data.get("available_capacity", 0),
            worker_count=data.get("worker_count", 0),
            ready_workers=data.get("ready_workers", 0),
        )

    async def create_session_token(
        self,
        ttl: int = 600,
        scopes: list[str] | None = None,
    ) -> SessionToken:
        """Create an ephemeral session token for client-side WebSocket auth.

        Session tokens are short-lived and designed for browser clients
        that need to connect directly to WebSocket endpoints without
        exposing long-lived API keys.

        Args:
            ttl: Time-to-live in seconds (60-3600). Default 600 (10 minutes).
            scopes: Requested scopes. Defaults to ["realtime"].
                    Cannot exceed the parent API key's scopes.

        Returns:
            SessionToken with the token and expiry info.

        Raises:
            PermissionError: If API key lacks 'realtime' scope.
            ValidationError: If invalid scopes or TTL.
        """
        payload: dict[str, Any] = {"ttl": ttl}
        if scopes is not None:
            payload["scopes"] = scopes

        try:
            response = await self._client.post(
                f"{self.base_url}/auth/tokens",
                json=payload,
                headers=self._headers(),
            )
        except httpx.ConnectError as e:
            raise ConnectError(f"Failed to connect: {e}") from e
        except httpx.TimeoutException as e:
            raise TimeoutException(f"Request timed out: {e}") from e

        if response.status_code != 201:
            _handle_error(response)

        data = response.json()
        return SessionToken(
            token=data["token"],
            expires_at=_parse_datetime(data["expires_at"]),
            scopes=data["scopes"],
            tenant_id=UUID(data["tenant_id"]),
        )
