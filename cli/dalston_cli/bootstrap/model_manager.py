"""Model readiness bootstrap for zero-config transcribe flow."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import httpx


class ModelBootstrapError(RuntimeError):
    """Raised when model bootstrap cannot make model ready."""

    def __init__(self, message: str, remediation: str | None = None):
        super().__init__(message)
        self.remediation = remediation


@dataclass(frozen=True)
class ModelEnsureResult:
    """Outcome for model ensure flow."""

    model_id: str
    pulled: bool


@dataclass(frozen=True)
class ModelStatus:
    """Current model registry status."""

    model_id: str
    status: str
    error: str | None = None


def resolve_bootstrap_model(requested_model: str, default_model: str) -> str:
    """Resolve model for zero-config local flow."""
    if requested_model.strip().lower() == "auto":
        return default_model
    return requested_model


def _headers(api_key: str | None) -> dict[str, str]:
    if not api_key:
        return {}
    return {"Authorization": f"Bearer {api_key}"}


def _parse_status(payload: dict[str, Any]) -> str:
    status = payload.get("status")
    if isinstance(status, str):
        return status
    return "unknown"


def _model_error(payload: dict[str, Any]) -> str | None:
    metadata = payload.get("metadata")
    if isinstance(metadata, dict):
        err = metadata.get("error")
        if isinstance(err, str) and err.strip():
            return err.strip()
    return None


def read_model_status(
    *,
    base_url: str,
    api_key: str | None,
    model_id: str,
    client: httpx.Client | None = None,
) -> ModelStatus:
    """Read model status without mutating server state."""
    owns_client = client is None
    http_client = client or httpx.Client(timeout=30.0)
    try:
        model = _get_model(
            http_client,
            base_url=base_url,
            api_key=api_key,
            model_id=model_id,
        )
        return ModelStatus(
            model_id=model_id,
            status=_parse_status(model),
            error=_model_error(model),
        )
    finally:
        if owns_client:
            http_client.close()


def _get_model(
    client: httpx.Client,
    *,
    base_url: str,
    api_key: str | None,
    model_id: str,
) -> dict[str, Any]:
    response = client.get(
        f"{base_url.rstrip('/')}/v1/models/{model_id}",
        headers=_headers(api_key),
    )
    if response.status_code == 404:
        raise ModelBootstrapError(
            f"Model '{model_id}' is not registered on this server.",
            remediation="Choose a valid --model or register the model first.",
        )
    if response.status_code >= 400:
        raise ModelBootstrapError(
            f"Failed to inspect model '{model_id}' (HTTP {response.status_code}).",
            remediation="Ensure the server is healthy and model API permissions are available.",
        )
    return response.json()


def _trigger_pull(
    client: httpx.Client,
    *,
    base_url: str,
    api_key: str | None,
    model_id: str,
) -> None:
    response = client.post(
        f"{base_url.rstrip('/')}/v1/models/{model_id}/pull",
        json={"force": False},
        headers=_headers(api_key),
    )
    if response.status_code >= 400:
        raise ModelBootstrapError(
            f"Failed to start model download for '{model_id}' (HTTP {response.status_code}).",
            remediation="Check model:pull permissions or run in DALSTON_BOOTSTRAP=false mode and pre-pull manually.",
        )


def ensure_model_ready(
    *,
    base_url: str,
    api_key: str | None,
    model_id: str,
    timeout_seconds: int,
    poll_interval_seconds: float = 1.0,
    client: httpx.Client | None = None,
) -> ModelEnsureResult:
    """Ensure model is ready by triggering pull and polling status."""
    owns_client = client is None
    http_client = client or httpx.Client(timeout=30.0)
    pulled = False

    try:
        model = _get_model(
            http_client,
            base_url=base_url,
            api_key=api_key,
            model_id=model_id,
        )
        status = _parse_status(model)
        if status == "ready":
            return ModelEnsureResult(model_id=model_id, pulled=False)

        if status in {"not_downloaded", "failed", "unknown"}:
            _trigger_pull(
                http_client,
                base_url=base_url,
                api_key=api_key,
                model_id=model_id,
            )
            pulled = True

        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            model = _get_model(
                http_client,
                base_url=base_url,
                api_key=api_key,
                model_id=model_id,
            )
            status = _parse_status(model)
            if status == "ready":
                return ModelEnsureResult(model_id=model_id, pulled=pulled)
            if status == "failed":
                message = _model_error(model) or "unknown model download error"
                raise ModelBootstrapError(
                    f"Model '{model_id}' failed to become ready: {message}",
                    remediation="Run `dalston models pull <model>` manually and inspect server logs.",
                )
            time.sleep(poll_interval_seconds)

        raise ModelBootstrapError(
            f"Timed out while waiting for model '{model_id}' to become ready.",
            remediation="Increase DALSTON_MODEL_ENSURE_TIMEOUT_SECONDS or pre-pull the model.",
        )
    finally:
        if owns_client:
            http_client.close()
