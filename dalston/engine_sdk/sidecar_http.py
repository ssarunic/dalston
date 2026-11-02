"""HTTP client for external inference sidecars.

Some engines are thin adapters: the Dalston contract (Redis streams,
heartbeats, S3, typed responses) lives in the Python process, while
inference runs in a separate server the adapter talks to over HTTP.  The
Riva engine does this over gRPC; the FluidAudio Mac worker (M104) does it
over plain HTTP against a Swift server on loopback.

This module holds the transport the adapters share: multipart POST of an
audio file plus form fields, JSON GETs for health and capabilities, and a
single error type so runners can tell a sidecar failure from an engine bug.
It knows nothing about any particular sidecar's routes or payloads.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import httpx
import structlog

logger = structlog.get_logger()

#: Default request timeout for inference calls. Matches the 2-hour
#: ``max_audio_duration`` most engines declare; a sidecar that is still
#: working on a file after that has hit a bug, not a long recording.
DEFAULT_INFERENCE_TIMEOUT_S = 7200.0

#: Health and capabilities probes must come back quickly or the heartbeat
#: thread stalls behind them.
PROBE_TIMEOUT_S = 5.0


class SidecarError(RuntimeError):
    """The sidecar answered with a non-2xx status or could not be reached.

    ``status_code`` is ``None`` when the request never got a response.
    """

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class SidecarHTTPClient:
    """Minimal HTTP client for an inference sidecar.

    Args:
        base_url: Scheme, host and port of the sidecar, no trailing path.
        timeout_s: Timeout for inference requests (``post_audio``).
    """

    def __init__(
        self,
        base_url: str,
        *,
        timeout_s: float = DEFAULT_INFERENCE_TIMEOUT_S,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s
        # ``transport`` lets tests substitute httpx.MockTransport.
        self._client = httpx.Client(
            base_url=self.base_url, timeout=timeout_s, transport=transport
        )

    @classmethod
    def from_env(
        cls,
        url_var: str,
        *,
        default_url: str,
        timeout_var: str | None = None,
    ) -> SidecarHTTPClient:
        """Build a client from ``url_var`` (and optionally ``timeout_var``)."""
        base_url = os.environ.get(url_var, default_url)
        timeout_s = DEFAULT_INFERENCE_TIMEOUT_S
        if timeout_var:
            timeout_s = float(os.environ.get(timeout_var, str(timeout_s)))
        return cls(base_url, timeout_s=timeout_s)

    # -- Probes --------------------------------------------------------------

    def get_json(
        self, path: str, *, timeout_s: float = PROBE_TIMEOUT_S
    ) -> dict[str, Any]:
        """GET ``path`` and return the decoded JSON object."""
        try:
            response = self._client.get(path, timeout=timeout_s)
        except httpx.HTTPError as exc:
            raise SidecarError(f"{self.base_url}{path}: {exc}") from exc
        return self._decode(response, path)

    def health(self, path: str = "/health") -> dict[str, Any]:
        """Return the sidecar's health document, or an ``unreachable`` stub.

        Never raises: this feeds the engine heartbeat, which must keep
        running while the sidecar restarts.
        """
        try:
            return self.get_json(path)
        except SidecarError as exc:
            return {
                "status": "unreachable",
                "url": self.base_url,
                "error": str(exc),
            }

    # -- Inference -----------------------------------------------------------

    def post_audio(
        self,
        path: str,
        audio_path: Path,
        *,
        fields: dict[str, Any] | None = None,
        field_name: str = "file",
        content_type: str = "audio/wav",
    ) -> dict[str, Any]:
        """Multipart-POST ``audio_path`` plus form ``fields`` and return JSON.

        ``None`` values in ``fields`` are omitted so callers can pass
        optional parameters straight through.
        """
        data = {k: str(v) for k, v in (fields or {}).items() if v is not None}
        try:
            with audio_path.open("rb") as fh:
                response = self._client.post(
                    path,
                    data=data,
                    files={field_name: (audio_path.name, fh, content_type)},
                )
        except httpx.HTTPError as exc:
            raise SidecarError(f"{self.base_url}{path}: {exc}") from exc
        return self._decode(response, path)

    # -- Lifecycle -----------------------------------------------------------

    def close(self) -> None:
        self._client.close()

    # -- Internals -----------------------------------------------------------

    def _decode(self, response: httpx.Response, path: str) -> dict[str, Any]:
        if response.status_code // 100 != 2:
            body = response.text.strip()
            raise SidecarError(
                f"{self.base_url}{path} returned {response.status_code}"
                + (f": {body[:500]}" if body else ""),
                status_code=response.status_code,
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise SidecarError(
                f"{self.base_url}{path} returned non-JSON body",
                status_code=response.status_code,
            ) from exc
        if not isinstance(payload, dict):
            raise SidecarError(
                f"{self.base_url}{path} returned {type(payload).__name__}, expected object",
                status_code=response.status_code,
            )
        return payload
