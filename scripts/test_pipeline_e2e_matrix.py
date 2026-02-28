#!/usr/bin/env python3
"""End-to-end pipeline matrix runner for Dalston.

This script validates key batch pipeline behaviors:
1. Core happy-path completion.
2. Per-channel branching.
3. Engine cold-start recovery (prepare/transcribe/merge).
4. Cancellation semantics.
5. Optional PII + audio-redaction branch (if engines are running).

By default it runs the full matrix. You can run a subset with ``--scenario``.

Examples:
    python scripts/test_pipeline_e2e_matrix.py
    python scripts/test_pipeline_e2e_matrix.py --scenario happy_path_default
    python scripts/test_pipeline_e2e_matrix.py --scenario prepare_cold_start --engine-mode wait
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

TERMINAL_JOB_STATUSES = {"completed", "failed", "cancelled"}
RUNNING_JOB_STATUSES = {"pending", "running", "cancelling"}
TASK_ACTIVE_STATUSES = {"pending", "ready", "running"}


class ScenarioError(RuntimeError):
    """Scenario failed."""


class ScenarioSkipped(RuntimeError):
    """Scenario skipped (environment not suitable)."""


@dataclass
class ScenarioResult:
    name: str
    status: str  # passed, failed, skipped
    duration_s: float
    details: str
    job_ids: list[str] = field(default_factory=list)


@dataclass
class RunContext:
    client: httpx.Client
    base_url: str
    api_key: str
    repo_root: Path
    audio_file: Path
    stereo_audio_file: Path
    long_audio_file: Path
    engine_mode: str  # wait, fail_fast, auto
    restart_delay_s: float
    timeout_s: int
    keep_services_down_on_failure: bool
    debug: bool
    debug_root: Path | None = None
    debug_state: DebugState | None = None


@dataclass
class DebugState:
    scenario_name: str
    scenario_dir: Path
    started_at_s: float
    timeline: list[dict[str, Any]] = field(default_factory=list)
    poll_signatures: dict[str, str] = field(default_factory=dict)


def log_info(message: str) -> None:
    print(f"[INFO] {message}", flush=True)


def log_warn(message: str) -> None:
    print(f"[WARN] {message}", flush=True)


def log_error(message: str) -> None:
    print(f"[ERROR] {message}", flush=True)


def _headers(api_key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key}"}


def _request_headers(api_key: str, request_id: str | None = None) -> dict[str, str]:
    headers = _headers(api_key)
    if request_id:
        headers["X-Request-ID"] = request_id
    return headers


def _load_api_key_from_env_file(repo_root: Path) -> str | None:
    env_path = repo_root / ".env"
    if not env_path.exists():
        return None

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("DALSTON_API_KEY="):
            _, value = line.split("=", 1)
            value = value.strip().strip('"').strip("'")
            return value or None
    return None


def _coerce_form_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _safe_name(value: str) -> str:
    allowed = []
    for ch in value:
        if ch.isalnum() or ch in ("-", "_"):
            allowed.append(ch)
        else:
            allowed.append("_")
    cleaned = "".join(allowed).strip("_")
    return cleaned or "scenario"


def _new_request_id(prefix: str = "e2e") -> str:
    return f"{prefix}_{uuid4().hex}"


def _debug_enabled(ctx: RunContext) -> bool:
    return bool(ctx.debug and ctx.debug_root)


def _debug_dump_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")


def _debug_dump_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _debug_event(ctx: RunContext, event: str, **data: Any) -> None:
    if not _debug_enabled(ctx) or ctx.debug_state is None:
        return
    ctx.debug_state.timeline.append(
        {
            "ts": _utc_now_iso(),
            "event": event,
            "data": data,
        }
    )


def _debug_start_scenario(ctx: RunContext, scenario_name: str) -> None:
    if not _debug_enabled(ctx):
        return

    assert ctx.debug_root is not None
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    scenario_dir = ctx.debug_root / f"{ts}_{_safe_name(scenario_name)}"
    scenario_dir.mkdir(parents=True, exist_ok=True)
    ctx.debug_state = DebugState(
        scenario_name=scenario_name,
        scenario_dir=scenario_dir,
        started_at_s=time.time(),
    )
    _debug_event(
        ctx,
        "scenario_start",
        scenario=scenario_name,
        base_url=ctx.base_url,
    )


def _debug_finish_scenario(ctx: RunContext) -> None:
    if not _debug_enabled(ctx) or ctx.debug_state is None:
        return

    state = ctx.debug_state
    _debug_event(
        ctx,
        "scenario_finish",
        duration_s=round(time.time() - state.started_at_s, 3),
    )
    _debug_dump_json(state.scenario_dir / "timeline.json", state.timeline)
    ctx.debug_state = None


def _debug_known_job_ids(ctx: RunContext) -> list[str]:
    if not _debug_enabled(ctx) or ctx.debug_state is None:
        return []

    ids: list[str] = []
    seen: set[str] = set()
    for entry in ctx.debug_state.timeline:
        data = entry.get("data", {})
        job_id = data.get("job_id")
        if isinstance(job_id, str) and job_id and job_id not in seen:
            seen.add(job_id)
            ids.append(job_id)
    return ids


def _debug_known_request_ids(ctx: RunContext) -> list[str]:
    if not _debug_enabled(ctx) or ctx.debug_state is None:
        return []

    ids: list[str] = []
    seen: set[str] = set()
    keys = ("request_id", "client_request_id", "response_request_id")
    for entry in ctx.debug_state.timeline:
        data = entry.get("data", {})
        for key in keys:
            value = data.get(key)
            if isinstance(value, str) and value and value not in seen:
                seen.add(value)
                ids.append(value)
    return ids


def _safe_get_job(ctx: RunContext, job_id: str) -> dict[str, Any]:
    try:
        return {"ok": True, "data": get_job(ctx, job_id)}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


def _safe_get_tasks(ctx: RunContext, job_id: str) -> dict[str, Any]:
    try:
        return {"ok": True, "data": list_tasks(ctx, job_id)}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


def _safe_get_engines(ctx: RunContext) -> dict[str, Any]:
    try:
        return {"ok": True, "data": list_engines(ctx)}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


def _safe_get_jaeger_json(
    ctx: RunContext,
    path: str,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    jaeger_url = os.environ.get("JAEGER_URL", "http://localhost:16686").rstrip("/")
    url = f"{jaeger_url}{path}"

    try:
        response = ctx.client.get(url, params=params, timeout=20)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "url": url, "params": params or {}, "error": str(exc)}

    payload: dict[str, Any]
    try:
        payload = response.json()
    except Exception:  # noqa: BLE001
        payload = {"raw": response.text}

    return {
        "ok": response.status_code == 200,
        "url": str(response.url),
        "status_code": response.status_code,
        "params": params or {},
        "data": payload,
    }


def _debug_capture_traces(
    ctx: RunContext,
    *,
    label: str,
    since_seconds: int,
    job_ids: list[str] | None = None,
    request_ids: list[str] | None = None,
) -> None:
    if not _debug_enabled(ctx) or ctx.debug_state is None:
        return

    services_resp = _safe_get_jaeger_json(ctx, "/api/services")
    traces_dir = ctx.debug_state.scenario_dir / "traces"
    traces_dir.mkdir(parents=True, exist_ok=True)
    _debug_dump_json(
        traces_dir / f"{_safe_name(label)}_services.json",
        {"captured_at": _utc_now_iso(), "response": services_resp},
    )

    services: list[str] = []
    if services_resp.get("ok"):
        raw_services = services_resp.get("data", {}).get("data", [])
        if isinstance(raw_services, list):
            services = [
                str(service)
                for service in raw_services
                if isinstance(service, str) and service.startswith("dalston-")
            ]

    if not services:
        _debug_event(
            ctx,
            "trace_capture_no_services",
            label=label,
            message="No dalston-* services in Jaeger",
        )
        return

    end_us = int(time.time() * 1_000_000)
    start_us = end_us - max(since_seconds, 60) * 1_000_000
    base_params = {
        "start": start_us,
        "end": end_us,
        "limit": 100,
    }

    jobs = list(dict.fromkeys(job_ids or []))
    reqs = list(dict.fromkeys(request_ids or []))

    for service in services:
        window_params = {"service": service, **base_params}
        window_resp = _safe_get_jaeger_json(ctx, "/api/traces", params=window_params)
        _debug_dump_json(
            traces_dir
            / f"{_safe_name(label)}_service_{_safe_name(service)}_window.json",
            {"captured_at": _utc_now_iso(), "response": window_resp},
        )

        for request_id in reqs:
            request_params = {
                "service": service,
                "tags": json.dumps({"dalston.request_id": request_id}),
                **base_params,
            }
            request_resp = _safe_get_jaeger_json(
                ctx,
                "/api/traces",
                params=request_params,
            )
            _debug_dump_json(
                traces_dir
                / (
                    f"{_safe_name(label)}_service_{_safe_name(service)}"
                    f"_request_{_safe_name(request_id)}.json"
                ),
                {"captured_at": _utc_now_iso(), "response": request_resp},
            )

        for job_id in jobs:
            job_params = {
                "service": service,
                "tags": json.dumps({"dalston.job_id": job_id}),
                **base_params,
            }
            job_resp = _safe_get_jaeger_json(ctx, "/api/traces", params=job_params)
            _debug_dump_json(
                traces_dir
                / (
                    f"{_safe_name(label)}_service_{_safe_name(service)}"
                    f"_job_{_safe_name(job_id)}.json"
                ),
                {"captured_at": _utc_now_iso(), "response": job_resp},
            )

    _debug_event(
        ctx,
        "trace_capture_complete",
        label=label,
        services=services,
        job_ids=jobs,
        request_ids=reqs,
        since_seconds=max(since_seconds, 60),
    )


def _debug_capture_job(ctx: RunContext, job_id: str, label: str) -> None:
    if not _debug_enabled(ctx) or ctx.debug_state is None:
        return

    payload = {
        "captured_at": _utc_now_iso(),
        "job_id": job_id,
        "job": _safe_get_job(ctx, job_id),
        "tasks": _safe_get_tasks(ctx, job_id),
    }
    _debug_dump_json(
        ctx.debug_state.scenario_dir / f"job_{_safe_name(label)}_{job_id}.json",
        payload,
    )


def _debug_capture_engines(ctx: RunContext, label: str) -> None:
    if not _debug_enabled(ctx) or ctx.debug_state is None:
        return

    payload = {
        "captured_at": _utc_now_iso(),
        "engines": _safe_get_engines(ctx),
    }
    _debug_dump_json(
        ctx.debug_state.scenario_dir / f"engines_{_safe_name(label)}.json",
        payload,
    )


def _debug_capture_compose_state(ctx: RunContext, label: str) -> None:
    if not _debug_enabled(ctx) or ctx.debug_state is None:
        return

    ps_services = run_compose(
        ctx, "ps", "--services", "--status", "running", check=False
    )
    ps_full = run_compose(ctx, "ps", check=False)

    payload = {
        "captured_at": _utc_now_iso(),
        "running_services_exit_code": ps_services.returncode,
        "running_services_stdout": ps_services.stdout,
        "running_services_stderr": ps_services.stderr,
        "compose_ps_exit_code": ps_full.returncode,
        "compose_ps_stdout": ps_full.stdout,
        "compose_ps_stderr": ps_full.stderr,
    }
    _debug_dump_json(
        ctx.debug_state.scenario_dir / f"compose_{_safe_name(label)}.json",
        payload,
    )


def _debug_capture_logs(
    ctx: RunContext,
    *,
    label: str,
    services: list[str],
    since_seconds: int,
) -> None:
    if not _debug_enabled(ctx) or ctx.debug_state is None or not services:
        return

    unique_services: list[str] = []
    seen: set[str] = set()
    for service in services:
        if service not in seen:
            seen.add(service)
            unique_services.append(service)

    logs_dir = ctx.debug_state.scenario_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    for service in unique_services:
        proc = run_compose(
            ctx,
            "logs",
            "--no-color",
            "--timestamps",
            "--since",
            f"{max(since_seconds, 30)}s",
            service,
            check=False,
        )
        filename = f"{_safe_name(label)}_{_safe_name(service)}.log"
        content = (
            f"# service: {service}\n"
            f"# captured_at: {_utc_now_iso()}\n"
            f"# exit_code: {proc.returncode}\n\n"
            f"{proc.stdout}"
        )
        if proc.stderr:
            content += f"\n\n# STDERR\n{proc.stderr}"
        _debug_dump_text(logs_dir / filename, content)


def _debug_record_job_poll(
    ctx: RunContext,
    *,
    poll_type: str,
    job_id: str,
    job: dict[str, Any],
) -> None:
    if not _debug_enabled(ctx) or ctx.debug_state is None:
        return

    status = str(job.get("status", "unknown"))
    stages = job.get("stages") if isinstance(job.get("stages"), list) else []
    stage_pairs = [
        f"{item.get('stage')}:{item.get('status')}"
        for item in stages
        if isinstance(item, dict)
    ]
    signature = f"{status}|{'|'.join(stage_pairs)}"
    prev_signature = ctx.debug_state.poll_signatures.get(job_id)
    if signature == prev_signature:
        return

    ctx.debug_state.poll_signatures[job_id] = signature
    _debug_event(
        ctx,
        "job_status_change",
        poll_type=poll_type,
        job_id=job_id,
        status=status,
        error=job.get("error"),
        stages=stage_pairs,
    )


def _debug_capture_failure_bundle(
    ctx: RunContext,
    *,
    error: str,
    started_at_s: float,
) -> None:
    if not _debug_enabled(ctx) or ctx.debug_state is None:
        return

    _debug_event(ctx, "scenario_failure", error=error)
    _debug_capture_compose_state(ctx, "failure")
    _debug_capture_engines(ctx, "failure")

    job_ids = _debug_known_job_ids(ctx)
    request_ids = _debug_known_request_ids(ctx)

    for job_id in job_ids:
        _debug_capture_job(ctx, job_id, "failure")

    elapsed = max(int(time.time() - started_at_s), 30)
    _debug_capture_traces(
        ctx,
        label="failure_window",
        since_seconds=elapsed + 120,
        job_ids=job_ids,
        request_ids=request_ids,
    )

    try:
        running = list_running_compose_services(ctx)
    except Exception:  # noqa: BLE001
        running = []

    engine_services = [s for s in running if s.startswith("stt-batch-")]
    services = ["orchestrator", *engine_services]
    _debug_capture_logs(
        ctx,
        label="failure_window",
        services=services,
        since_seconds=elapsed + 120,
    )


def ensure_file_exists(path: Path) -> None:
    if not path.exists():
        raise ScenarioError(f"Required file not found: {path}")


def check_gateway_health(ctx: RunContext) -> None:
    try:
        response = ctx.client.get(f"{ctx.base_url}/health", timeout=15)
    except httpx.HTTPError as exc:
        raise ScenarioError(
            f"Cannot reach gateway at {ctx.base_url}/health: {exc}"
        ) from exc

    if response.status_code != 200:
        raise ScenarioError(
            f"Gateway health check failed: HTTP {response.status_code} | {response.text}"
        )


def submit_job(
    ctx: RunContext,
    audio_file: Path,
    *,
    model: str = "auto",
    speaker_detection: str = "none",
    timestamps_granularity: str = "word",
    extra_fields: dict[str, Any] | None = None,
) -> str:
    client_request_id = _new_request_id("e2e_submit") if _debug_enabled(ctx) else None
    data: dict[str, str] = {
        "model": model,
        "language": "auto",
        "speaker_detection": speaker_detection,
        "timestamps_granularity": timestamps_granularity,
    }
    if extra_fields:
        for key, value in extra_fields.items():
            if value is None:
                continue
            data[key] = _coerce_form_value(value)

    with audio_file.open("rb") as handle:
        files = {"file": (audio_file.name, handle, "audio/wav")}
        try:
            response = ctx.client.post(
                f"{ctx.base_url}/v1/audio/transcriptions",
                headers=_request_headers(ctx.api_key, client_request_id),
                data=data,
                files=files,
                timeout=90,
            )
        except httpx.HTTPError as exc:
            raise ScenarioError(f"Job submission failed: {exc}") from exc

    if response.status_code != 201:
        raise ScenarioError(
            f"Job submission failed: HTTP {response.status_code} | {response.text}"
        )

    payload = response.json()
    job_id = payload.get("id")
    if not job_id:
        raise ScenarioError(f"Job submission response missing id: {payload}")
    _debug_event(
        ctx,
        "submit_job_http",
        client_request_id=client_request_id,
        response_request_id=response.headers.get("x-request-id"),
        job_id=str(job_id),
        status_code=response.status_code,
        model=model,
    )
    return str(job_id)


def get_job(ctx: RunContext, job_id: str) -> dict[str, Any]:
    try:
        response = ctx.client.get(
            f"{ctx.base_url}/v1/audio/transcriptions/{job_id}",
            headers=_headers(ctx.api_key),
            timeout=30,
        )
    except httpx.HTTPError as exc:
        raise ScenarioError(f"Failed to fetch job {job_id}: {exc}") from exc

    if response.status_code != 200:
        raise ScenarioError(
            f"Failed to fetch job {job_id}: HTTP {response.status_code} | {response.text}"
        )
    return response.json()


def list_tasks(ctx: RunContext, job_id: str) -> list[dict[str, Any]]:
    try:
        response = ctx.client.get(
            f"{ctx.base_url}/v1/audio/transcriptions/{job_id}/tasks",
            headers=_headers(ctx.api_key),
            timeout=30,
        )
    except httpx.HTTPError as exc:
        raise ScenarioError(f"Failed to list tasks for {job_id}: {exc}") from exc

    if response.status_code != 200:
        raise ScenarioError(
            f"Failed to list tasks for {job_id}: "
            f"HTTP {response.status_code} | {response.text}"
        )
    payload = response.json()
    return payload.get("tasks", [])


def try_list_tasks(ctx: RunContext, job_id: str) -> list[dict[str, Any]]:
    """Best-effort task list; returns empty list if tasks are not yet materialized."""
    try:
        response = ctx.client.get(
            f"{ctx.base_url}/v1/audio/transcriptions/{job_id}/tasks",
            headers=_headers(ctx.api_key),
            timeout=15,
        )
    except httpx.HTTPError:
        return []
    if response.status_code != 200:
        return []
    return response.json().get("tasks", [])


def cancel_job(ctx: RunContext, job_id: str) -> dict[str, Any]:
    client_request_id = _new_request_id("e2e_cancel") if _debug_enabled(ctx) else None
    try:
        response = ctx.client.post(
            f"{ctx.base_url}/v1/audio/transcriptions/{job_id}/cancel",
            headers=_request_headers(ctx.api_key, client_request_id),
            timeout=30,
        )
    except httpx.HTTPError as exc:
        raise ScenarioError(f"Failed to cancel job {job_id}: {exc}") from exc

    if response.status_code != 200:
        raise ScenarioError(
            f"Cancel failed for {job_id}: HTTP {response.status_code} | {response.text}"
        )
    _debug_event(
        ctx,
        "cancel_job_http",
        client_request_id=client_request_id,
        response_request_id=response.headers.get("x-request-id"),
        job_id=job_id,
        status_code=response.status_code,
    )
    return response.json()


def list_engines(ctx: RunContext) -> list[dict[str, Any]]:
    client_request_id = _new_request_id("e2e_engines") if _debug_enabled(ctx) else None
    try:
        response = ctx.client.get(
            f"{ctx.base_url}/v1/engines",
            headers=_request_headers(ctx.api_key, client_request_id),
            timeout=30,
        )
    except httpx.HTTPError as exc:
        raise ScenarioError(f"Failed to list engines: {exc}") from exc

    if response.status_code != 200:
        raise ScenarioError(
            f"Failed to list engines: HTTP {response.status_code} | {response.text}"
        )
    _debug_event(
        ctx,
        "list_engines_http",
        client_request_id=client_request_id,
        response_request_id=response.headers.get("x-request-id"),
        status_code=response.status_code,
    )
    payload = response.json()
    return payload.get("engines", [])


def wait_for_terminal_job(
    ctx: RunContext, job_id: str, timeout_s: int
) -> dict[str, Any]:
    deadline = time.time() + timeout_s
    last_status = "unknown"

    while time.time() < deadline:
        job = get_job(ctx, job_id)
        status = job.get("status", "unknown")
        last_status = str(status)
        _debug_record_job_poll(ctx, poll_type="terminal", job_id=job_id, job=job)
        if status in TERMINAL_JOB_STATUSES:
            return job
        time.sleep(1)

    raise ScenarioError(
        f"Timed out waiting for job {job_id} to become terminal "
        f"(last status: {last_status})"
    )


def wait_for_running_or_terminal(
    ctx: RunContext, job_id: str, timeout_s: int
) -> dict[str, Any]:
    deadline = time.time() + timeout_s
    last_status = "unknown"

    while time.time() < deadline:
        job = get_job(ctx, job_id)
        status = str(job.get("status", "unknown"))
        last_status = status
        _debug_record_job_poll(
            ctx, poll_type="running_or_terminal", job_id=job_id, job=job
        )
        if status == "running" or status in TERMINAL_JOB_STATUSES:
            return job
        time.sleep(1)

    raise ScenarioError(
        f"Timed out waiting for job {job_id} to reach running/terminal "
        f"(last status: {last_status})"
    )


def _require_stage(
    tasks: list[dict[str, Any]],
    *,
    stage: str,
    allowed_statuses: set[str],
    exact: bool = True,
) -> list[dict[str, Any]]:
    if exact:
        matches = [t for t in tasks if str(t.get("stage")) == stage]
    else:
        matches = [t for t in tasks if str(t.get("stage", "")).startswith(stage)]

    if not matches:
        raise ScenarioError(f"Missing expected stage '{stage}' in task list")

    bad = [t for t in matches if str(t.get("status")) not in allowed_statuses]
    if bad:
        bad_states = ", ".join(f"{t.get('stage')}={t.get('status')}" for t in bad)
        raise ScenarioError(
            f"Unexpected status for stage '{stage}': {bad_states}; "
            f"allowed={sorted(allowed_statuses)}"
        )
    return matches


def run_compose(
    ctx: RunContext, *args: str, check: bool = True
) -> subprocess.CompletedProcess:
    command = ["docker", "compose", *args]
    proc = subprocess.run(
        command,
        cwd=ctx.repo_root,
        capture_output=True,
        text=True,
    )
    if check and proc.returncode != 0:
        raise ScenarioError(
            "docker compose command failed:\n"
            f"  cmd: {' '.join(command)}\n"
            f"  code: {proc.returncode}\n"
            f"  stdout: {proc.stdout.strip()}\n"
            f"  stderr: {proc.stderr.strip()}"
        )
    return proc


def list_running_compose_services(ctx: RunContext) -> list[str]:
    proc = run_compose(ctx, "ps", "--services", "--status", "running")
    return [line.strip() for line in proc.stdout.splitlines() if line.strip()]


def get_service_engine_id(ctx: RunContext, service: str) -> str | None:
    """Read ENGINE_ID from a running compose service container."""
    proc = run_compose(ctx, "exec", "-T", service, "env", check=False)
    if proc.returncode != 0:
        return None
    for raw_line in proc.stdout.splitlines():
        line = raw_line.strip()
        if line.startswith("ENGINE_ID="):
            _, value = line.split("=", 1)
            value = value.strip()
            return value or None
    return None


def get_preferred_transcribe_model(ctx: RunContext) -> str:
    """Pick a running transcribe ENGINE_ID for deterministic scenario submissions."""
    running_services = list_running_compose_services(ctx)
    transcribe_services = sorted(
        service
        for service in running_services
        if service.startswith("stt-batch-transcribe-")
    )
    for service in transcribe_services:
        engine_id = get_service_engine_id(ctx, service)
        if engine_id:
            return engine_id

    # Fallback: use API registry if compose service inspection is unavailable.
    running_transcribe_engines = [
        engine
        for engine in list_engines(ctx)
        if str(engine.get("stage")) == "transcribe"
        and str(engine.get("status")) == "running"
    ]
    if running_transcribe_engines:
        return str(running_transcribe_engines[0].get("id"))

    return "auto"


def ensure_services_up(ctx: RunContext, services: list[str]) -> None:
    if not services:
        return
    run_compose(ctx, "up", "-d", *services)


def stop_services(ctx: RunContext, services: list[str]) -> None:
    if not services:
        return
    run_compose(ctx, "stop", *services)


def _observe_unavailable_behavior(
    ctx: RunContext,
    job_id: str,
    stage_hint: str,
    timeout_s: int,
) -> str:
    """Observe behavior while engine service is down.

    Returns:
        "fail_fast": job failed quickly while stage was unavailable
        "waiting": task for the stage remains active while waiting for engine
        "undetermined": neither observed inside timeout window
    """
    deadline = time.time() + timeout_s

    while time.time() < deadline:
        job = get_job(ctx, job_id)
        status = str(job.get("status"))
        _debug_record_job_poll(
            ctx, poll_type="engine_unavailable_observe", job_id=job_id, job=job
        )
        if status == "failed":
            return "fail_fast"

        tasks = try_list_tasks(ctx, job_id)
        stage_tasks = [
            task for task in tasks if str(task.get("stage", "")).startswith(stage_hint)
        ]

        if any(str(task.get("status")) == "failed" for task in stage_tasks):
            return "fail_fast"

        if any(str(task.get("status")) in TASK_ACTIVE_STATUSES for task in stage_tasks):
            return "waiting"

        time.sleep(1)

    return "undetermined"


def scenario_happy_path_default(ctx: RunContext) -> tuple[str, list[str]]:
    model = get_preferred_transcribe_model(ctx)
    job_id = submit_job(
        ctx,
        ctx.audio_file,
        model=model,
    )
    log_info(f"happy_path_default submitted: {job_id}")
    _debug_event(
        ctx, "job_submitted", scenario="happy_path_default", job_id=job_id, model=model
    )
    _debug_capture_job(ctx, job_id, "happy_path_default_submitted")
    job = wait_for_terminal_job(ctx, job_id, timeout_s=ctx.timeout_s)
    _debug_capture_job(ctx, job_id, "happy_path_default_terminal")

    if job.get("status") != "completed":
        raise ScenarioError(
            f"Expected completed, got {job.get('status')} | error={job.get('error')}"
        )

    tasks = list_tasks(ctx, job_id)
    _require_stage(tasks, stage="prepare", allowed_statuses={"completed"})
    _require_stage(
        tasks, stage="transcribe", allowed_statuses={"completed"}, exact=False
    )
    _require_stage(tasks, stage="merge", allowed_statuses={"completed"})

    if not job.get("text"):
        raise ScenarioError("Completed job has empty transcript text")
    if not job.get("segments"):
        raise ScenarioError("Completed job has no segments")

    details = (
        f"completed with {len(job.get('segments') or [])} segments; "
        f"stages prepare/transcribe/merge all completed"
    )
    return details, [job_id]


def scenario_per_channel_pipeline(ctx: RunContext) -> tuple[str, list[str]]:
    model = get_preferred_transcribe_model(ctx)
    job_id = submit_job(
        ctx,
        ctx.stereo_audio_file,
        model=model,
        speaker_detection="per_channel",
        timestamps_granularity="word",
    )
    log_info(f"per_channel_pipeline submitted: {job_id}")
    _debug_event(
        ctx,
        "job_submitted",
        scenario="per_channel_pipeline",
        job_id=job_id,
        model=model,
    )
    _debug_capture_job(ctx, job_id, "per_channel_pipeline_submitted")
    job = wait_for_terminal_job(ctx, job_id, timeout_s=ctx.timeout_s)
    _debug_capture_job(ctx, job_id, "per_channel_pipeline_terminal")

    if job.get("status") != "completed":
        raise ScenarioError(
            f"Expected completed, got {job.get('status')} | error={job.get('error')}"
        )

    tasks = list_tasks(ctx, job_id)
    _require_stage(tasks, stage="prepare", allowed_statuses={"completed"})
    _require_stage(tasks, stage="transcribe_ch0", allowed_statuses={"completed"})
    _require_stage(tasks, stage="transcribe_ch1", allowed_statuses={"completed"})
    _require_stage(tasks, stage="merge", allowed_statuses={"completed"})

    speakers = job.get("speakers") or []
    if len(speakers) < 2:
        raise ScenarioError(
            f"Expected at least 2 speakers for per-channel output, got {len(speakers)}"
        )

    details = (
        f"completed with {len(speakers)} speakers and per-channel transcribe stages"
    )
    return details, [job_id]


def _run_cold_start_scenario(
    ctx: RunContext,
    *,
    scenario_name: str,
    services_to_pause: list[str],
    stage_hint: str,
    audio_file: Path,
    submit_kwargs: dict[str, Any] | None = None,
) -> tuple[str, list[str]]:
    if not services_to_pause:
        raise ScenarioSkipped(f"{scenario_name}: no running services to pause")

    _debug_event(
        ctx,
        "cold_start_begin",
        scenario=scenario_name,
        services_to_pause=services_to_pause,
        stage_hint=stage_hint,
    )
    _debug_capture_compose_state(ctx, f"{scenario_name}_before_pause")
    _debug_capture_engines(ctx, f"{scenario_name}_before_pause")

    ensure_services_up(ctx, services_to_pause)
    running = set(list_running_compose_services(ctx))
    paused = [service for service in services_to_pause if service in running]
    if not paused:
        raise ScenarioSkipped(
            f"{scenario_name}: services not running ({', '.join(services_to_pause)})"
        )

    job_ids: list[str] = []
    failed = False

    stop_services(ctx, paused)
    log_info(f"{scenario_name}: paused services {paused}")
    _debug_event(ctx, "services_paused", scenario=scenario_name, services=paused)
    _debug_capture_engines(ctx, f"{scenario_name}_after_pause")
    _debug_capture_compose_state(ctx, f"{scenario_name}_after_pause")

    try:
        job_id = submit_job(ctx, audio_file, **(submit_kwargs or {}))
        job_ids.append(job_id)
        log_info(f"{scenario_name}: submitted {job_id}")
        _debug_event(
            ctx,
            "job_submitted",
            scenario=scenario_name,
            job_id=job_id,
            submit_kwargs=submit_kwargs or {},
        )
        _debug_capture_job(ctx, job_id, f"{scenario_name}_submitted")

        observe_timeout = max(6, int(ctx.restart_delay_s))
        observed = _observe_unavailable_behavior(
            ctx,
            job_id=job_id,
            stage_hint=stage_hint,
            timeout_s=observe_timeout,
        )
        log_info(f"{scenario_name}: observed behavior={observed}")
        _debug_event(
            ctx,
            "cold_start_observed_behavior",
            scenario=scenario_name,
            job_id=job_id,
            observed=observed,
        )
        _debug_capture_job(ctx, job_id, f"{scenario_name}_observed")
        _debug_capture_engines(ctx, f"{scenario_name}_observed")

        if ctx.engine_mode == "fail_fast":
            terminal = wait_for_terminal_job(
                ctx, job_id, timeout_s=min(ctx.timeout_s, 180)
            )
            _debug_capture_job(ctx, job_id, f"{scenario_name}_terminal_fail_fast_mode")
            if terminal.get("status") != "failed":
                raise ScenarioError(
                    f"{scenario_name}: expected fail_fast but got "
                    f"{terminal.get('status')}"
                )
            return "engine unavailable failed fast as expected", job_ids

        if observed == "fail_fast":
            if ctx.engine_mode == "wait":
                raise ScenarioError(
                    f"{scenario_name}: expected wait mode but observed fail_fast"
                )
            _debug_capture_job(
                ctx, job_id, f"{scenario_name}_terminal_observed_fail_fast"
            )
            return "observed fail_fast behavior (auto mode)", job_ids

        # wait/auto non-fail-fast path: bring services back and ensure completion
        if ctx.restart_delay_s > 0:
            time.sleep(ctx.restart_delay_s)
        ensure_services_up(ctx, paused)
        log_info(f"{scenario_name}: resumed services {paused}")
        _debug_event(ctx, "services_resumed", scenario=scenario_name, services=paused)
        _debug_capture_engines(ctx, f"{scenario_name}_after_resume")
        _debug_capture_compose_state(ctx, f"{scenario_name}_after_resume")
        _debug_capture_job(ctx, job_id, f"{scenario_name}_after_resume")

        terminal = wait_for_terminal_job(ctx, job_id, timeout_s=ctx.timeout_s)
        _debug_capture_job(ctx, job_id, f"{scenario_name}_terminal")
        if terminal.get("status") != "completed":
            raise ScenarioError(
                f"{scenario_name}: expected completion after resume, got "
                f"{terminal.get('status')} | error={terminal.get('error')}"
            )

        tasks = list_tasks(ctx, job_id)
        _require_stage(
            tasks, stage=stage_hint, allowed_statuses={"completed"}, exact=False
        )
        _debug_event(
            ctx,
            "cold_start_completed",
            scenario=scenario_name,
            job_id=job_id,
            stage_hint=stage_hint,
        )
        return "waited while engine was down, then resumed and completed", job_ids
    except Exception:
        failed = True
        raise
    finally:
        if paused and not (failed and ctx.keep_services_down_on_failure):
            ensure_services_up(ctx, paused)


def scenario_prepare_cold_start(ctx: RunContext) -> tuple[str, list[str]]:
    return _run_cold_start_scenario(
        ctx,
        scenario_name="prepare_cold_start",
        services_to_pause=["stt-batch-prepare"],
        stage_hint="prepare",
        audio_file=ctx.audio_file,
        submit_kwargs={"model": get_preferred_transcribe_model(ctx)},
    )


def scenario_transcribe_cold_start(ctx: RunContext) -> tuple[str, list[str]]:
    running = list_running_compose_services(ctx)
    transcribe_services = sorted(
        service for service in running if service.startswith("stt-batch-transcribe-")
    )
    if not transcribe_services:
        raise ScenarioSkipped("transcribe_cold_start: no running transcribe services")

    # Pick a concrete running transcribe service and derive its ENGINE_ID.
    target_service: str | None = None
    target_engine: str | None = None
    for service in transcribe_services:
        engine_id = get_service_engine_id(ctx, service)
        if engine_id:
            target_service = service
            target_engine = engine_id
            break

    if not target_service or not target_engine:
        raise ScenarioSkipped(
            "transcribe_cold_start: unable to derive ENGINE_ID from running "
            "transcribe service"
        )

    return _run_cold_start_scenario(
        ctx,
        scenario_name="transcribe_cold_start",
        services_to_pause=[target_service],
        stage_hint="transcribe",
        audio_file=ctx.audio_file,
        submit_kwargs={"model": target_engine},
    )


def scenario_merge_cold_start(ctx: RunContext) -> tuple[str, list[str]]:
    return _run_cold_start_scenario(
        ctx,
        scenario_name="merge_cold_start",
        services_to_pause=["stt-batch-merge"],
        stage_hint="merge",
        audio_file=ctx.audio_file,
        submit_kwargs={"model": get_preferred_transcribe_model(ctx)},
    )


def scenario_cancellation_running_job(ctx: RunContext) -> tuple[str, list[str]]:
    model = get_preferred_transcribe_model(ctx)
    job_id = submit_job(
        ctx,
        ctx.long_audio_file,
        model=model,
    )
    log_info(f"cancellation_running_job submitted: {job_id}")
    _debug_event(
        ctx,
        "job_submitted",
        scenario="cancellation_running_job",
        job_id=job_id,
        model=model,
    )
    _debug_capture_job(ctx, job_id, "cancellation_running_job_submitted")

    job = wait_for_running_or_terminal(ctx, job_id, timeout_s=45)
    status = str(job.get("status"))
    if status in TERMINAL_JOB_STATUSES:
        raise ScenarioSkipped(
            f"Job became terminal before cancellation test (status={status})"
        )

    cancel_payload = cancel_job(ctx, job_id)
    cancel_status = str(cancel_payload.get("status"))
    _debug_event(
        ctx,
        "job_cancel_requested",
        scenario="cancellation_running_job",
        job_id=job_id,
        cancel_status=cancel_status,
    )
    _debug_capture_job(ctx, job_id, "cancellation_running_job_after_cancel")
    if cancel_status not in {"cancelling", "cancelled"}:
        raise ScenarioError(f"Unexpected cancel response status: {cancel_status}")

    terminal = wait_for_terminal_job(ctx, job_id, timeout_s=ctx.timeout_s)
    _debug_capture_job(ctx, job_id, "cancellation_running_job_terminal")
    if terminal.get("status") != "cancelled":
        raise ScenarioError(
            f"Expected cancelled final status, got {terminal.get('status')}"
        )

    details = "cancel accepted while running and final status reached cancelled"
    return details, [job_id]


def scenario_optional_pii_redaction(ctx: RunContext) -> tuple[str, list[str]]:
    running_stages = {
        str(engine.get("stage"))
        for engine in list_engines(ctx)
        if str(engine.get("status")) == "running"
    }

    required = {"pii_detect", "audio_redact"}
    if not required.issubset(running_stages):
        missing = sorted(required - running_stages)
        raise ScenarioSkipped(
            f"Required engines not running for optional branch: missing stages {missing}"
        )

    model = get_preferred_transcribe_model(ctx)
    job_id = submit_job(
        ctx,
        ctx.long_audio_file,
        model=model,
        extra_fields={
            "pii_detection": True,
            "redact_pii_audio": True,
            "pii_redaction_mode": "beep",
        },
    )
    log_info(f"optional_pii_redaction submitted: {job_id}")
    _debug_event(
        ctx,
        "job_submitted",
        scenario="optional_pii_redaction",
        job_id=job_id,
        model=model,
    )
    _debug_capture_job(ctx, job_id, "optional_pii_redaction_submitted")

    terminal = wait_for_terminal_job(ctx, job_id, timeout_s=ctx.timeout_s)
    _debug_capture_job(ctx, job_id, "optional_pii_redaction_terminal")
    if terminal.get("status") != "completed":
        raise ScenarioError(
            f"Expected completed PII job, got {terminal.get('status')} | "
            f"error={terminal.get('error')}"
        )

    tasks = list_tasks(ctx, job_id)
    _require_stage(
        tasks, stage="pii_detect", allowed_statuses={"completed"}, exact=False
    )
    _require_stage(
        tasks,
        stage="audio_redact",
        allowed_statuses={"completed", "skipped"},
        exact=False,
    )
    _require_stage(tasks, stage="merge", allowed_statuses={"completed"})

    details = (
        "PII detection + redaction branch completed (or redaction skipped cleanly)"
    )
    return details, [job_id]


SCENARIOS: dict[str, Callable[[RunContext], tuple[str, list[str]]]] = {
    "happy_path_default": scenario_happy_path_default,
    "per_channel_pipeline": scenario_per_channel_pipeline,
    "prepare_cold_start": scenario_prepare_cold_start,
    "transcribe_cold_start": scenario_transcribe_cold_start,
    "merge_cold_start": scenario_merge_cold_start,
    "cancellation_running_job": scenario_cancellation_running_job,
    "optional_pii_redaction": scenario_optional_pii_redaction,
}

DEFAULT_SCENARIO_ORDER = [
    "happy_path_default",
    "per_channel_pipeline",
    "prepare_cold_start",
    "transcribe_cold_start",
    "merge_cold_start",
    "cancellation_running_job",
    "optional_pii_redaction",
]


def run_scenario(
    ctx: RunContext,
    name: str,
    fn: Callable[[RunContext], tuple[str, list[str]]],
) -> ScenarioResult:
    started = time.time()
    log_info(f"Running scenario: {name}")
    _debug_start_scenario(ctx, name)
    _debug_capture_compose_state(ctx, "start")
    _debug_capture_engines(ctx, "start")
    _debug_capture_traces(
        ctx,
        label="start_window",
        since_seconds=120,
    )

    try:
        details, job_ids = fn(ctx)
        _debug_capture_traces(
            ctx,
            label="end_window",
            since_seconds=max(int(time.time() - started), 30) + 120,
            job_ids=job_ids,
            request_ids=_debug_known_request_ids(ctx),
        )
        _debug_event(
            ctx, "scenario_result", status="passed", details=details, job_ids=job_ids
        )
        return ScenarioResult(
            name=name,
            status="passed",
            duration_s=time.time() - started,
            details=details,
            job_ids=job_ids,
        )
    except ScenarioSkipped as exc:
        _debug_capture_traces(
            ctx,
            label="end_window",
            since_seconds=max(int(time.time() - started), 30) + 120,
            job_ids=_debug_known_job_ids(ctx),
            request_ids=_debug_known_request_ids(ctx),
        )
        _debug_event(ctx, "scenario_result", status="skipped", details=str(exc))
        return ScenarioResult(
            name=name,
            status="skipped",
            duration_s=time.time() - started,
            details=str(exc),
        )
    except Exception as exc:
        _debug_capture_failure_bundle(
            ctx,
            error=str(exc),
            started_at_s=started,
        )
        _debug_event(ctx, "scenario_result", status="failed", details=str(exc))
        return ScenarioResult(
            name=name,
            status="failed",
            duration_s=time.time() - started,
            details=str(exc),
        )
    finally:
        _debug_finish_scenario(ctx)


def resolve_selected_scenarios(values: list[str] | None) -> list[str]:
    if not values or "all" in values:
        return list(DEFAULT_SCENARIO_ORDER)

    selected: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value not in SCENARIOS:
            raise ScenarioError(f"Unknown scenario: {value}")
        if value not in seen:
            selected.append(value)
            seen.add(value)
    return selected


def build_parser(
    default_audio: Path, default_stereo: Path, default_long: Path
) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-url",
        default=os.environ.get("DALSTON_GATEWAY_URL", "http://localhost:8000"),
        help="Gateway base URL (default: %(default)s)",
    )
    parser.add_argument(
        "--api-key",
        default=None,
        help=(
            "API key with jobs:read/jobs:write scopes "
            "(defaults to DALSTON_API_KEY env, then .env DALSTON_API_KEY, then test-key)"
        ),
    )
    parser.add_argument(
        "--audio",
        type=Path,
        default=default_audio,
        help=f"Mono test audio (default: {default_audio})",
    )
    parser.add_argument(
        "--stereo-audio",
        type=Path,
        default=default_stereo,
        help=f"Stereo test audio (default: {default_stereo})",
    )
    parser.add_argument(
        "--long-audio",
        type=Path,
        default=default_long,
        help=f"Long audio for cancellation/optional branches (default: {default_long})",
    )
    parser.add_argument(
        "--scenario",
        action="append",
        choices=["all", *SCENARIOS.keys()],
        help=(
            "Scenario to run (repeatable). "
            "Default: run full matrix. Use 'all' to force full matrix."
        ),
    )
    parser.add_argument(
        "--engine-mode",
        choices=["wait", "fail_fast", "auto"],
        default="auto",
        help=(
            "Expected behavior when required engine is down. "
            "'wait': must resume after restart, "
            "'fail_fast': must fail, "
            "'auto': accept observed behavior."
        ),
    )
    parser.add_argument(
        "--restart-delay",
        type=float,
        default=6.0,
        help="Seconds to wait before restarting paused engine services (default: %(default)s)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=300,
        help="Per-scenario completion timeout in seconds (default: %(default)s)",
    )
    parser.add_argument(
        "--keep-services-down-on-failure",
        action="store_true",
        help="Do not auto-restart paused services after a failed cold-start scenario.",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help=(
            "Enable deep debug artifact capture (job/task timelines, "
            "engine snapshots, compose state, orchestrator/engine logs on failure)."
        ),
    )
    parser.add_argument(
        "--debug-dir",
        type=Path,
        default=None,
        help=(
            "Base directory for debug artifacts. "
            "Default when --debug is enabled: artifacts/e2e_debug/<timestamp>/"
        ),
    )
    return parser


def print_summary(results: list[ScenarioResult]) -> None:
    print("\n" + "=" * 78)
    print("PIPELINE E2E MATRIX SUMMARY")
    print("=" * 78)

    for result in results:
        jobs = f" | jobs={','.join(result.job_ids)}" if result.job_ids else ""
        print(
            f"[{result.status.upper():7}] {result.name:28} "
            f"{result.duration_s:6.1f}s | {result.details}{jobs}"
        )

    passed = sum(1 for r in results if r.status == "passed")
    failed = sum(1 for r in results if r.status == "failed")
    skipped = sum(1 for r in results if r.status == "skipped")
    print("-" * 78)
    print(
        f"Total: {len(results)} | Passed: {passed} | Failed: {failed} | Skipped: {skipped}"
    )
    print("=" * 78)


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    default_audio = repo_root / "tests" / "audio" / "test_merged.wav"
    default_stereo = repo_root / "tests" / "audio" / "test_stereo_speakers.wav"
    default_long = repo_root / "tests" / "audio" / "large" / "test_pii_combined.wav"
    parser = build_parser(default_audio, default_stereo, default_long)
    args = parser.parse_args()

    api_key = (
        args.api_key
        or os.environ.get("DALSTON_API_KEY")
        or _load_api_key_from_env_file(repo_root)
        or "test-key"
    )

    selected = resolve_selected_scenarios(args.scenario)

    audio_file = args.audio.resolve()
    stereo_audio_file = args.stereo_audio.resolve()
    long_audio_file = args.long_audio.resolve()
    ensure_file_exists(audio_file)
    ensure_file_exists(stereo_audio_file)
    ensure_file_exists(long_audio_file)

    debug_root: Path | None = None
    if args.debug:
        base_debug_dir = (
            args.debug_dir.resolve()
            if args.debug_dir
            else (repo_root / "artifacts" / "e2e_debug")
        )
        run_stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        debug_root = base_debug_dir / f"run_{run_stamp}"
        debug_root.mkdir(parents=True, exist_ok=True)
        _debug_dump_json(
            debug_root / "run_metadata.json",
            {
                "created_at": _utc_now_iso(),
                "base_url": args.base_url.rstrip("/"),
                "jaeger_url": os.environ.get("JAEGER_URL", "http://localhost:16686"),
                "selected_scenarios": selected,
                "engine_mode": args.engine_mode,
                "timeout_s": max(args.timeout, 30),
                "restart_delay_s": max(args.restart_delay, 0.0),
                "audio_file": str(audio_file),
                "stereo_audio_file": str(stereo_audio_file),
                "long_audio_file": str(long_audio_file),
            },
        )

    timeout = httpx.Timeout(connect=10.0, read=60.0, write=60.0, pool=10.0)
    with httpx.Client(timeout=timeout) as client:
        ctx = RunContext(
            client=client,
            base_url=args.base_url.rstrip("/"),
            api_key=api_key,
            repo_root=repo_root,
            audio_file=audio_file,
            stereo_audio_file=stereo_audio_file,
            long_audio_file=long_audio_file,
            engine_mode=args.engine_mode,
            restart_delay_s=max(args.restart_delay, 0.0),
            timeout_s=max(args.timeout, 30),
            keep_services_down_on_failure=args.keep_services_down_on_failure,
            debug=args.debug,
            debug_root=debug_root,
        )

        check_gateway_health(ctx)
        log_info(f"Gateway healthy at {ctx.base_url}")
        if args.debug and debug_root is not None:
            log_info(f"Debug artifacts directory: {debug_root}")
        if ctx.api_key == "test-key":
            log_warn(
                "Using fallback API key 'test-key'. "
                "If auth fails, set DALSTON_API_KEY or pass --api-key."
            )
        log_info(f"Selected scenarios: {', '.join(selected)}")
        log_info(f"Engine unavailable mode: {ctx.engine_mode}")

        results: list[ScenarioResult] = []
        for scenario_name in selected:
            fn = SCENARIOS[scenario_name]
            result = run_scenario(ctx, scenario_name, fn)
            results.append(result)
            status = result.status.upper()
            print(
                f"[{status}] {scenario_name} ({result.duration_s:.1f}s) - {result.details}",
                flush=True,
            )

    print_summary(results)
    if any(result.status == "failed" for result in results):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
