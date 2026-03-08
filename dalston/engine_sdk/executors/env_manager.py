"""Helpers for resolving and validating runtime-specific virtualenvs."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class VenvEnvironment:
    runtime: str
    python_executable: Path
    env_dir: Path
    lockfile: Path | None = None


class VenvEnvironmentManager:
    """Resolve runtime-specific Python executables with caching and health checks."""

    def __init__(
        self,
        *,
        runtime_pythons: dict[str, Path | str] | None = None,
        runtime_lockfiles: dict[str, Path | str] | None = None,
        health_check_timeout_s: float = 30.0,
    ) -> None:
        if health_check_timeout_s <= 0:
            raise ValueError("health_check_timeout_s must be > 0")
        self._runtime_pythons = {
            runtime: Path(path) for runtime, path in (runtime_pythons or {}).items()
        }
        self._runtime_lockfiles = {
            runtime: Path(path) for runtime, path in (runtime_lockfiles or {}).items()
        }
        self._cache: dict[str, VenvEnvironment] = {}
        self._health_check_timeout_s = health_check_timeout_s

    def register_runtime(
        self,
        runtime: str,
        *,
        python_executable: Path | str,
        lockfile: Path | str | None = None,
    ) -> None:
        self._runtime_pythons[runtime] = Path(python_executable)
        if lockfile is not None:
            self._runtime_lockfiles[runtime] = Path(lockfile)
        self._cache.pop(runtime, None)

    def ensure_environment(self, runtime: str) -> VenvEnvironment:
        cached = self._cache.get(runtime)
        if cached is not None:
            return cached

        python_executable = self._runtime_pythons.get(runtime)
        if python_executable is None:
            raise FileNotFoundError(
                f"No venv python configured for runtime '{runtime}'"
            )

        resolved_python = python_executable.expanduser().absolute()
        if not resolved_python.exists() or not resolved_python.is_file():
            raise FileNotFoundError(
                f"Configured venv python does not exist for runtime '{runtime}': "
                f"{resolved_python}"
            )

        lockfile = self._runtime_lockfiles.get(runtime)
        resolved_lockfile = None
        if lockfile is not None:
            resolved_lockfile = lockfile.expanduser().resolve()
            if not resolved_lockfile.exists() or not resolved_lockfile.is_file():
                raise FileNotFoundError(
                    f"Configured venv lockfile does not exist for runtime "
                    f"'{runtime}': {resolved_lockfile}"
                )

        self._health_check(
            runtime,
            resolved_python,
            timeout_s=self._health_check_timeout_s,
        )

        environment = VenvEnvironment(
            runtime=runtime,
            python_executable=resolved_python,
            env_dir=resolved_python.parent.parent,
            lockfile=resolved_lockfile,
        )
        self._cache[runtime] = environment
        return environment

    def clear_cache(self) -> None:
        self._cache.clear()

    @staticmethod
    def _health_check(
        runtime: str,
        python_executable: Path,
        *,
        timeout_s: float,
    ) -> None:
        try:
            completed = subprocess.run(
                [
                    str(python_executable),
                    "-c",
                    "import dalston, sys; print(sys.executable)",
                ],
                capture_output=True,
                text=True,
                check=False,
                timeout=timeout_s,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                f"Health check timed out for runtime '{runtime}' venv "
                f"({python_executable}) after {timeout_s:.0f}s"
            ) from exc
        if completed.returncode != 0:
            stderr = completed.stderr.strip() or completed.stdout.strip()
            raise RuntimeError(
                f"Health check failed for runtime '{runtime}' venv "
                f"({python_executable}): {stderr}"
            )
