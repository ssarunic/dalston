"""Tests for env-configurable task timeout defaults.

Covers the two hardcoded 600s fallbacks that surfaced when a pyannote
diarize task hit ``Task processing exceeded 600s timeout`` — one on the
engine SDK runner (``DEFAULT_TASK_TIMEOUT``) and one on the orchestrator
reconciler (``REENQUEUE_TIMEOUT_S``). Both now default to the shared
``TASK_UNKNOWN_DURATION_TIMEOUT_S`` (1h) and accept env overrides.
"""

from __future__ import annotations

import importlib
import sys

import pytest


def _fresh_import(dotted: str):
    """Drop the module from sys.modules and re-import it.

    Module-level constants that read env at import time need a fresh
    module load per test; ``importlib.reload`` is fragile in pytest
    when autouse fixtures have already touched the environment.
    """
    sys.modules.pop(dotted, None)
    return importlib.import_module(dotted)


@pytest.mark.parametrize(
    "env_value,expected",
    [(None, 3600), ("900", 900)],
)
def test_engine_sdk_default_task_timeout(
    monkeypatch, env_value: str | None, expected: int
) -> None:
    """``DEFAULT_TASK_TIMEOUT`` honours ``DALSTON_DEFAULT_TASK_TIMEOUT_S``."""
    if env_value is None:
        monkeypatch.delenv("DALSTON_DEFAULT_TASK_TIMEOUT_S", raising=False)
    else:
        monkeypatch.setenv("DALSTON_DEFAULT_TASK_TIMEOUT_S", env_value)

    runner = _fresh_import("dalston.engine_sdk.runner")
    assert runner.EngineRunner.DEFAULT_TASK_TIMEOUT == expected


@pytest.mark.parametrize(
    "env_value,expected",
    [(None, 3600), ("1800", 1800)],
)
def test_reconciler_reenqueue_timeout(
    monkeypatch, env_value: str | None, expected: int
) -> None:
    """``REENQUEUE_TIMEOUT_S`` honours ``DALSTON_RECONCILER_REENQUEUE_TIMEOUT_S``."""
    if env_value is None:
        monkeypatch.delenv("DALSTON_RECONCILER_REENQUEUE_TIMEOUT_S", raising=False)
    else:
        monkeypatch.setenv("DALSTON_RECONCILER_REENQUEUE_TIMEOUT_S", env_value)

    reconciler = _fresh_import("dalston.orchestrator.reconciler")
    assert reconciler.REENQUEUE_TIMEOUT_S == expected


def test_shared_constant_is_used_by_both_fallbacks(monkeypatch) -> None:
    """Without env overrides, both fallbacks match the shared constant."""
    monkeypatch.delenv("DALSTON_DEFAULT_TASK_TIMEOUT_S", raising=False)
    monkeypatch.delenv("DALSTON_RECONCILER_REENQUEUE_TIMEOUT_S", raising=False)

    from dalston.common.timeouts import TASK_UNKNOWN_DURATION_TIMEOUT_S

    runner = _fresh_import("dalston.engine_sdk.runner")
    reconciler = _fresh_import("dalston.orchestrator.reconciler")

    assert runner.EngineRunner.DEFAULT_TASK_TIMEOUT == TASK_UNKNOWN_DURATION_TIMEOUT_S
    assert reconciler.REENQUEUE_TIMEOUT_S == TASK_UNKNOWN_DURATION_TIMEOUT_S
