"""Unit tests for FasterWhisperModelManager CUDA error handling."""

from __future__ import annotations

import sys
import types

import pytest

from dalston.engine_sdk.managers.faster_whisper import (
    FasterWhisperModelManager,
    _is_missing_cuda_shared_library_error,
)


def test_detects_missing_cuda_shared_library_error() -> None:
    err = RuntimeError("Library libcublas.so.12 is not found or cannot be loaded")
    assert _is_missing_cuda_shared_library_error(err) is True


def test_ignores_unrelated_model_load_error() -> None:
    err = RuntimeError("Model file not found")
    assert _is_missing_cuda_shared_library_error(err) is False


def test_wraps_cuda_shared_library_failure_with_actionable_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailingWhisperModel:
        def __init__(self, *args, **kwargs):  # noqa: ANN002, ANN003
            raise RuntimeError(
                "Library libcublas.so.12 is not found or cannot be loaded"
            )

    fake_module = types.ModuleType("faster_whisper")
    fake_module.WhisperModel = FailingWhisperModel
    monkeypatch.setitem(sys.modules, "faster_whisper", fake_module)

    manager = FasterWhisperModelManager(device="cuda", compute_type="float16")
    try:
        with pytest.raises(RuntimeError, match="CUDA shared libraries are unavailable"):
            manager._load_model("large-v3-turbo")
    finally:
        manager.shutdown()
