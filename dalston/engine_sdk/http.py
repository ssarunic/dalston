"""HTTP transport helpers for presigned-URL-based task I/O (M77).

Engines fetch their input.json and store their output.json via plain HTTP
using presigned URLs embedded in the task metadata. No S3 credentials or
boto3 required in engine containers.

Both functions retry on 5xx responses with exponential backoff and raise
``EngineTransportError`` on non-2xx after all attempts are exhausted, so the
runner can classify transport failures distinctly from engine logic failures.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import httpx
import structlog

logger = structlog.get_logger()

# Retry configuration
_MAX_ATTEMPTS = 4
_BASE_BACKOFF_S = 0.5
_TIMEOUT_S = 30.0


class EngineTransportError(Exception):
    """Raised when HTTP transport fails after all retry attempts."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def fetch_json(url: str) -> dict[str, Any]:
    """HTTP GET a JSON resource from a presigned URL.

    Retries on 5xx responses with exponential backoff.

    Args:
        url: Presigned GET URL for the resource.

    Returns:
        Parsed JSON as a dictionary.

    Raises:
        EngineTransportError: On non-2xx after retries, or on network error.
    """
    last_exc: Exception | None = None

    for attempt in range(_MAX_ATTEMPTS):
        try:
            with httpx.Client(timeout=_TIMEOUT_S) as client:
                response = client.get(url)

            if response.is_success:
                return response.json()

            # 4xx are permanent failures — do not retry
            if 400 <= response.status_code < 500:
                raise EngineTransportError(
                    f"fetch_json: HTTP {response.status_code} (permanent)",
                    status_code=response.status_code,
                )

            # 5xx — transient, retry
            logger.warning(
                "fetch_json_retrying",
                attempt=attempt + 1,
                status_code=response.status_code,
            )
            last_exc = EngineTransportError(
                f"fetch_json: HTTP {response.status_code}",
                status_code=response.status_code,
            )

        except EngineTransportError:
            raise
        except Exception as exc:
            logger.warning(
                "fetch_json_network_error", attempt=attempt + 1, error=str(exc)
            )
            last_exc = EngineTransportError(f"fetch_json: network error: {exc}")

        if attempt < _MAX_ATTEMPTS - 1:
            time.sleep(_BASE_BACKOFF_S * (2**attempt))

    raise last_exc or EngineTransportError("fetch_json: all attempts failed")


def put_json(url: str, data: dict[str, Any]) -> None:
    """HTTP PUT a JSON payload to a presigned URL.

    Retries on 5xx responses with exponential backoff.

    Args:
        url: Presigned PUT URL for the destination.
        data: Dictionary to serialise and upload.

    Raises:
        EngineTransportError: On non-2xx after retries, or on network error.
    """
    body = json.dumps(data, default=str).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    last_exc: Exception | None = None

    for attempt in range(_MAX_ATTEMPTS):
        try:
            with httpx.Client(timeout=_TIMEOUT_S) as client:
                response = client.put(url, content=body, headers=headers)

            if response.is_success:
                return

            if 400 <= response.status_code < 500:
                raise EngineTransportError(
                    f"put_json: HTTP {response.status_code} (permanent)",
                    status_code=response.status_code,
                )

            logger.warning(
                "put_json_retrying",
                attempt=attempt + 1,
                status_code=response.status_code,
            )
            last_exc = EngineTransportError(
                f"put_json: HTTP {response.status_code}",
                status_code=response.status_code,
            )

        except EngineTransportError:
            raise
        except Exception as exc:
            logger.warning(
                "put_json_network_error", attempt=attempt + 1, error=str(exc)
            )
            last_exc = EngineTransportError(f"put_json: network error: {exc}")

        if attempt < _MAX_ATTEMPTS - 1:
            time.sleep(_BASE_BACKOFF_S * (2**attempt))

    raise last_exc or EngineTransportError("put_json: all attempts failed")


_STREAM_CHUNK_SIZE = 1024 * 1024  # 1 MiB


def stream_to_file(url: str, destination: Path) -> None:
    """HTTP GET a presigned URL, streaming directly to a file.

    Avoids buffering the entire artifact in memory — critical for large audio
    files that could otherwise cause OOM on long recordings.

    Retries on 5xx responses with exponential backoff. On retry the partial
    file is truncated and the download restarts from the beginning.

    Args:
        url: Presigned GET URL for the resource.
        destination: Local path to write the downloaded bytes into.

    Raises:
        EngineTransportError: On non-2xx after retries, or on network error.
    """
    last_exc: Exception | None = None
    destination.parent.mkdir(parents=True, exist_ok=True)

    for attempt in range(_MAX_ATTEMPTS):
        try:
            with httpx.Client(timeout=_TIMEOUT_S) as client:
                with client.stream("GET", url) as response:
                    if not response.is_success:
                        # Consume body so connection is properly closed
                        response.read()
                        if 400 <= response.status_code < 500:
                            raise EngineTransportError(
                                f"stream_to_file: HTTP {response.status_code} (permanent)",
                                status_code=response.status_code,
                            )
                        logger.warning(
                            "stream_to_file_retrying",
                            attempt=attempt + 1,
                            status_code=response.status_code,
                        )
                        last_exc = EngineTransportError(
                            f"stream_to_file: HTTP {response.status_code}",
                            status_code=response.status_code,
                        )
                    else:
                        with destination.open("wb") as fh:
                            for chunk in response.iter_bytes(
                                chunk_size=_STREAM_CHUNK_SIZE
                            ):
                                fh.write(chunk)
                        return

        except EngineTransportError:
            raise
        except Exception as exc:
            logger.warning(
                "stream_to_file_network_error", attempt=attempt + 1, error=str(exc)
            )
            last_exc = EngineTransportError(f"stream_to_file: network error: {exc}")

        if attempt < _MAX_ATTEMPTS - 1:
            time.sleep(_BASE_BACKOFF_S * (2**attempt))

    raise last_exc or EngineTransportError("stream_to_file: all attempts failed")


def stream_from_file(
    url: str, source: Path, content_type: str = "application/octet-stream"
) -> None:
    """HTTP PUT a file to a presigned URL, streaming directly from disk.

    Avoids loading the entire artifact into memory — critical for large audio
    files that could otherwise cause OOM on long recordings.

    Retries on 5xx responses with exponential backoff.

    Args:
        url: Presigned PUT URL for the destination.
        source: Local path to read and upload.
        content_type: MIME type for the Content-Type header.

    Raises:
        EngineTransportError: On non-2xx after retries, or on network error.
    """
    headers = {"Content-Type": content_type}
    last_exc: Exception | None = None

    for attempt in range(_MAX_ATTEMPTS):
        try:
            with source.open("rb") as fh:
                with httpx.Client(timeout=_TIMEOUT_S) as client:
                    response = client.put(url, content=fh, headers=headers)

            if response.is_success:
                return

            if 400 <= response.status_code < 500:
                raise EngineTransportError(
                    f"stream_from_file: HTTP {response.status_code} (permanent)",
                    status_code=response.status_code,
                )

            logger.warning(
                "stream_from_file_retrying",
                attempt=attempt + 1,
                status_code=response.status_code,
            )
            last_exc = EngineTransportError(
                f"stream_from_file: HTTP {response.status_code}",
                status_code=response.status_code,
            )

        except EngineTransportError:
            raise
        except Exception as exc:
            logger.warning(
                "stream_from_file_network_error", attempt=attempt + 1, error=str(exc)
            )
            last_exc = EngineTransportError(f"stream_from_file: network error: {exc}")

        if attempt < _MAX_ATTEMPTS - 1:
            time.sleep(_BASE_BACKOFF_S * (2**attempt))

    raise last_exc or EngineTransportError("stream_from_file: all attempts failed")
