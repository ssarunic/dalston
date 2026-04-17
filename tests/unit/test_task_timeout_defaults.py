"""Tests for env-configurable task timeout defaults.

Covers the two hardcoded 600s fallbacks that surfaced when a pyannote
diarize task hit ``Task processing exceeded 600s timeout`` — one on the
engine SDK runner (DEFAULT_TASK_TIMEOUT) and one on the orchestrator
reconciler (re-enqueue timeout).

These are module-constant reads at import time, so we re-import the
module fresh to exercise the env-var path without relying on
``importlib.reload`` (which is fragile in pytest when the autouse env
restore fixture has already run).
"""

from __future__ import annotations

import importlib
import sys


def _fresh_import(dotted: str):
    """Drop the module from sys.modules and re-import it."""
    sys.modules.pop(dotted, None)
    return importlib.import_module(dotted)


def test_engine_sdk_runner_default_is_1h(monkeypatch) -> None:
    """DEFAULT_TASK_TIMEOUT defaults to 3600 when env var is unset."""
    monkeypatch.delenv("DALSTON_DEFAULT_TASK_TIMEOUT_S", raising=False)
    runner = _fresh_import("dalston.engine_sdk.runner")
    assert runner.EngineRunner.DEFAULT_TASK_TIMEOUT == 3600


def test_engine_sdk_runner_timeout_env_override(monkeypatch) -> None:
    """DALSTON_DEFAULT_TASK_TIMEOUT_S overrides the class attribute."""
    monkeypatch.setenv("DALSTON_DEFAULT_TASK_TIMEOUT_S", "900")
    runner = _fresh_import("dalston.engine_sdk.runner")
    assert runner.EngineRunner.DEFAULT_TASK_TIMEOUT == 900


def test_reconciler_reenqueue_timeout_default_is_1h(monkeypatch) -> None:
    """Reconciler's re-enqueue timeout defaults to 3600."""
    monkeypatch.delenv("DALSTON_RECONCILER_REENQUEUE_TIMEOUT_S", raising=False)
    reconciler = _fresh_import("dalston.orchestrator.reconciler")
    assert reconciler._REENQUEUE_TIMEOUT_S == 3600


def test_reconciler_reenqueue_timeout_env_override(monkeypatch) -> None:
    """DALSTON_RECONCILER_REENQUEUE_TIMEOUT_S overrides the module constant."""
    monkeypatch.setenv("DALSTON_RECONCILER_REENQUEUE_TIMEOUT_S", "1800")
    reconciler = _fresh_import("dalston.orchestrator.reconciler")
    assert reconciler._REENQUEUE_TIMEOUT_S == 1800
