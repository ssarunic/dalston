"""Helpers for resolving and validating engine_id-specific virtualenvs."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class VenvEnvironment:
    engine_id: str
    python_executable: Path
    env_dir: Path
    lockfile: Path | None = None


class VenvEnvironmentManager:
    """Resolve engine_id-specific Python executables with caching and health checks."""

    def __init__(
        self,
        *,
        runtime_pythons: dict[str, Path | str] | None = None,
        runtime_lockfiles: dict[str, Path | str] | None = None,
        health_check_timeout_s: float = 30.0,
    ) -> None:
        if health_check_timeout_s <= 0:
            raise ValueError("health_check_timeout_s must be > 0")
        self._engine_id_pythons = {
            engine_id: Path(path) for engine_id, path in (runtime_pythons or {}).items()
        }
        self._engine_id_lockfiles = {
            engine_id: Path(path)
            for engine_id, path in (runtime_lockfiles or {}).items()
        }
        self._cache: dict[str, VenvEnvironment] = {}
        self._health_check_timeout_s = health_check_timeout_s

    def register_engine_id(
        self,
        engine_id: str,
        *,
        python_executable: Path | str,
        lockfile: Path | str | None = None,
    ) -> None:
        self._engine_id_pythons[engine_id] = Path(python_executable)
        if lockfile is not None:
            self._engine_id_lockfiles[engine_id] = Path(lockfile)
        self._cache.pop(engine_id, None)

    def ensure_environment(self, engine_id: str) -> VenvEnvironment:
        cached = self._cache.get(engine_id)
        if cached is not None:
            return cached

        python_executable = self._engine_id_pythons.get(engine_id)
        if python_executable is None:
            raise FileNotFoundError(
                f"No venv python configured for engine_id '{engine_id}'"
            )

        resolved_python = python_executable.expanduser().absolute()
        if not resolved_python.exists() or not resolved_python.is_file():
            raise FileNotFoundError(
                f"Configured venv python does not exist for engine_id '{engine_id}': "
                f"{resolved_python}"
            )

        lockfile = self._engine_id_lockfiles.get(engine_id)
        resolved_lockfile = None
        if lockfile is not None:
            resolved_lockfile = lockfile.expanduser().resolve()
            if not resolved_lockfile.exists() or not resolved_lockfile.is_file():
                raise FileNotFoundError(
                    f"Configured venv lockfile does not exist for engine_id "
                    f"'{engine_id}': {resolved_lockfile}"
                )

        self._health_check(
            engine_id,
            resolved_python,
            timeout_s=self._health_check_timeout_s,
        )

        environment = VenvEnvironment(
            engine_id=engine_id,
            python_executable=resolved_python,
            env_dir=resolved_python.parent.parent,
            lockfile=resolved_lockfile,
        )
        self._cache[engine_id] = environment
        return environment

    def clear_cache(self) -> None:
        self._cache.clear()

    @staticmethod
    def _health_check(
        engine_id: str,
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
                f"Health check timed out for engine_id '{engine_id}' venv "
                f"({python_executable}) after {timeout_s:.0f}s"
            ) from exc
        if completed.returncode != 0:
            stderr = completed.stderr.strip() or completed.stdout.strip()
            raise RuntimeError(
                f"Health check failed for engine_id '{engine_id}' venv "
                f"({python_executable}): {stderr}"
            )
