"""Preflight checks for CLI bootstrap state machine."""

from __future__ import annotations

import shutil
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from dalston_cli.bootstrap.settings import BootstrapSettings


class PreflightError(RuntimeError):
    """Preflight error with optional remediation hint."""

    def __init__(self, message: str, remediation: str | None = None):
        super().__init__(message)
        self.remediation = remediation


@dataclass(frozen=True)
class PreflightReport:
    """Summary of successful preflight checks."""

    checked_files: tuple[Path, ...]


def _assert_writable_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    probe = path / ".preflight-write-check"
    try:
        probe.write_text("ok", encoding="utf-8")
    except OSError as exc:
        raise PreflightError(
            f"Directory is not writable: {path}",
            remediation=f"Ensure write permission for {path}.",
        ) from exc
    finally:
        probe.unlink(missing_ok=True)


def _check_disk_space(path: Path, min_free_bytes: int) -> None:
    if min_free_bytes <= 0:
        return
    free_bytes = shutil.disk_usage(path).free
    if free_bytes < min_free_bytes:
        free_mib = free_bytes // (1024 * 1024)
        needed_mib = min_free_bytes // (1024 * 1024)
        raise PreflightError(
            f"Insufficient free disk space ({free_mib} MiB available, {needed_mib} MiB required).",
            remediation="Free disk space or lower DALSTON_BOOTSTRAP_MIN_FREE_BYTES.",
        )


def _check_server_start_tool(settings: BootstrapSettings) -> None:
    if not settings.enabled:
        return
    if shutil.which("uvicorn") is None:
        raise PreflightError(
            "Missing required tool: uvicorn",
            remediation='Install gateway extras, for example: pip install -e ".[gateway]".',
        )


def _validate_input_files(files: Iterable[Path]) -> tuple[Path, ...]:
    checked: list[Path] = []
    for file_path in files:
        if not file_path.exists():
            raise PreflightError(
                f"Input file not found: {file_path}",
                remediation="Check the file path and try again.",
            )
        if not file_path.is_file():
            raise PreflightError(
                f"Input path is not a file: {file_path}",
                remediation="Provide a regular audio file path.",
            )
        if not file_path.stat().st_size:
            raise PreflightError(
                f"Input file is empty: {file_path}",
                remediation="Provide a non-empty audio file.",
            )
        try:
            with file_path.open("rb"):
                pass
        except OSError as exc:
            raise PreflightError(
                f"Input file is not readable: {file_path}",
                remediation="Adjust file permissions and try again.",
            ) from exc
        checked.append(file_path)
    return tuple(checked)


def run_preflight(
    *,
    files: Iterable[Path],
    settings: BootstrapSettings,
) -> PreflightReport:
    """Run deterministic preflight checks before any bootstrap side effects."""
    checked_files = _validate_input_files(files)
    _assert_writable_directory(settings.run_dir)
    _assert_writable_directory(settings.log_dir)
    _check_disk_space(settings.run_dir, settings.min_free_bytes)
    _check_server_start_tool(settings)
    return PreflightReport(checked_files=checked_files)
