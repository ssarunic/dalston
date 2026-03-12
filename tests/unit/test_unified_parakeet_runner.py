"""Tests for the unified Parakeet and Parakeet-ONNX runners.

Covers:
- BatchRejectedError inherits from TaskDeferredError (both runners)
- Runner class structure (shared core slot, not running at init)
- admitted_process rejects batch tasks when admission is full
- admitted_process releases the slot after a successful call
- admitted_process releases the slot when the underlying call raises
- _register_engine_modules resolves correct module names / file paths
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock, patch

import pytest

from dalston.engine_sdk.admission import (
    AdmissionConfig,
    AdmissionController,
    TaskDeferredError,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_ENGINES_ROOT = Path("engines")
_PARAKEET_RUNNER_PATH = _ENGINES_ROOT / "stt-unified" / "parakeet" / "runner.py"
_PARAKEET_ONNX_RUNNER_PATH = (
    _ENGINES_ROOT / "stt-unified" / "parakeet-onnx" / "runner.py"
)

# Prefixes of module names we inject — only these are cleaned up between tests.
# Third-party packages (numpy, torch, etc.) are intentionally left in sys.modules
# because Python C extensions cannot be re-imported in the same process.
_INJECTED_PREFIXES = ("parakeet_runner_", "parakeet_onnx_runner_")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_runner_module(path: Path, module_name: str) -> ModuleType:
    """Load a runner module from a file path outside the normal package tree."""
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None and spec.loader is not None, f"Could not load {path}"
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(autouse=True)
def _cleanup_runner_modules():
    """Remove only the dynamically injected runner module names after each test.

    We must NOT remove third-party packages (e.g. numpy, torch): Python C
    extensions cannot be re-loaded in the same process and doing so causes
    ``ImportError: cannot load module more than once per process``.
    """
    yield
    for key in list(sys.modules):
        if any(key.startswith(p) for p in _INJECTED_PREFIXES):
            sys.modules.pop(key, None)


# ---------------------------------------------------------------------------
# BatchRejectedError inheritance
# ---------------------------------------------------------------------------


class TestBatchRejectedErrorInheritance:
    """BatchRejectedError must subclass TaskDeferredError in both runners."""

    def test_parakeet_batch_rejected_error_is_task_deferred(self) -> None:
        module = _load_runner_module(_PARAKEET_RUNNER_PATH, "parakeet_runner_mod")
        assert issubclass(module.BatchRejectedError, TaskDeferredError)

    def test_parakeet_onnx_batch_rejected_error_is_task_deferred(self) -> None:
        module = _load_runner_module(
            _PARAKEET_ONNX_RUNNER_PATH, "parakeet_onnx_runner_mod"
        )
        assert issubclass(module.BatchRejectedError, TaskDeferredError)


# ---------------------------------------------------------------------------
# Runner class structure
# ---------------------------------------------------------------------------


class TestUnifiedRunnerClassStructure:
    """Both runner classes expose the expected interface without instantiation."""

    def test_parakeet_runner_class_exists(self) -> None:
        module = _load_runner_module(_PARAKEET_RUNNER_PATH, "parakeet_runner_cls")
        cls = module.UnifiedParakeetRunner
        assert callable(getattr(cls, "run", None))
        assert hasattr(cls, "_run_async")
        assert hasattr(cls, "_shutdown")
        assert hasattr(cls, "_run_batch")

    def test_parakeet_onnx_runner_class_exists(self) -> None:
        module = _load_runner_module(
            _PARAKEET_ONNX_RUNNER_PATH, "parakeet_onnx_runner_cls"
        )
        cls = module.UnifiedParakeetOnnxRunner
        assert callable(getattr(cls, "run", None))
        assert hasattr(cls, "_run_async")
        assert hasattr(cls, "_shutdown")
        assert hasattr(cls, "_run_batch")

    def test_parakeet_runner_uses_parakeet_core(self) -> None:
        """Runner module must import and reference NemoInference."""
        module = _load_runner_module(_PARAKEET_RUNNER_PATH, "parakeet_runner_core_ref")
        from dalston.engine_sdk.inference.nemo_inference import NemoInference

        assert module.NemoInference is NemoInference

    def test_parakeet_onnx_runner_uses_parakeet_onnx_core(self) -> None:
        """Runner module must import and reference OnnxInference."""
        module = _load_runner_module(
            _PARAKEET_ONNX_RUNNER_PATH, "parakeet_onnx_runner_core_ref"
        )
        from dalston.engine_sdk.inference.onnx_inference import OnnxInference

        assert module.OnnxInference is OnnxInference


# ---------------------------------------------------------------------------
# admitted_process: admission gate around batch engine.process
#
# These tests re-implement the admitted_process closure from the runner using
# the same logic, avoiding the need to actually instantiate the runner (which
# would attempt to load NeMo models).
# ---------------------------------------------------------------------------


def _make_admitted_process(
    controller: AdmissionController, original_process, BatchRejectedError
):
    """Replicate the admitted_process closure from both runner implementations."""

    def admitted_process(engine_input, ctx):
        if not controller.admit_batch():
            raise BatchRejectedError("Admission controller rejected batch task")
        try:
            return original_process(engine_input, ctx)
        finally:
            controller.release_batch()

    return admitted_process


class TestAdmittedProcessLogic:
    """The admitted_process closure must gate and release admission correctly."""

    @pytest.mark.parametrize(
        "runner_path,module_name",
        [
            (_PARAKEET_RUNNER_PATH, "parakeet_runner_adm"),
            (_PARAKEET_ONNX_RUNNER_PATH, "parakeet_onnx_runner_adm"),
        ],
    )
    def test_rejects_when_batch_slots_full(self, runner_path, module_name) -> None:
        """admitted_process raises BatchRejectedError when no batch slot is free."""
        module = _load_runner_module(runner_path, module_name)

        controller = AdmissionController(
            AdmissionConfig(rt_reservation=1, batch_max_inflight=1, total_capacity=2)
        )
        assert controller.admit_batch() is True  # fill the one batch slot

        original_process = MagicMock(return_value="ok")
        admitted_process = _make_admitted_process(
            controller, original_process, module.BatchRejectedError
        )

        mock_input = MagicMock()
        mock_input.task_id = "task-001"

        with pytest.raises(module.BatchRejectedError):
            admitted_process(mock_input, MagicMock())

        original_process.assert_not_called()

    @pytest.mark.parametrize(
        "runner_path,module_name",
        [
            (_PARAKEET_RUNNER_PATH, "parakeet_runner_adm2"),
            (_PARAKEET_ONNX_RUNNER_PATH, "parakeet_onnx_runner_adm2"),
        ],
    )
    def test_releases_slot_after_successful_call(
        self, runner_path, module_name
    ) -> None:
        """admitted_process releases the batch slot on success, allowing a retry."""
        module = _load_runner_module(runner_path, module_name)

        controller = AdmissionController(
            AdmissionConfig(rt_reservation=0, batch_max_inflight=1, total_capacity=1)
        )
        original_process = MagicMock(return_value="result")
        admitted_process = _make_admitted_process(
            controller, original_process, module.BatchRejectedError
        )

        mock_input = MagicMock()
        mock_input.task_id = "task-002"

        result = admitted_process(mock_input, MagicMock())
        assert result == "result"

        # Slot must be released — a second call must succeed
        result2 = admitted_process(mock_input, MagicMock())
        assert result2 == "result"
        assert original_process.call_count == 2

    @pytest.mark.parametrize(
        "runner_path,module_name",
        [
            (_PARAKEET_RUNNER_PATH, "parakeet_runner_adm3"),
            (_PARAKEET_ONNX_RUNNER_PATH, "parakeet_onnx_runner_adm3"),
        ],
    )
    def test_releases_slot_on_exception(self, runner_path, module_name) -> None:
        """admitted_process releases the slot when the underlying call raises."""
        module = _load_runner_module(runner_path, module_name)

        controller = AdmissionController(
            AdmissionConfig(rt_reservation=0, batch_max_inflight=1, total_capacity=1)
        )
        original_process = MagicMock(side_effect=RuntimeError("transcription failed"))
        admitted_process = _make_admitted_process(
            controller, original_process, module.BatchRejectedError
        )

        with pytest.raises(RuntimeError, match="transcription failed"):
            admitted_process(MagicMock(), MagicMock())

        # After the exception the slot must be free
        assert controller.admit_batch() is True

    @pytest.mark.parametrize(
        "runner_path,module_name",
        [
            (_PARAKEET_RUNNER_PATH, "parakeet_runner_adm4"),
            (_PARAKEET_ONNX_RUNNER_PATH, "parakeet_onnx_runner_adm4"),
        ],
    )
    def test_rt_rejected_when_at_full_capacity(self, runner_path, module_name) -> None:
        """RT admission is rejected when all slots are occupied by batch tasks."""
        _load_runner_module(runner_path, module_name)

        controller = AdmissionController(
            AdmissionConfig(rt_reservation=0, batch_max_inflight=1, total_capacity=1)
        )
        assert controller.admit_batch() is True

        assert controller.admit_rt() is False

        controller.release_batch()
        assert controller.admit_rt() is True


# ---------------------------------------------------------------------------
# _register_engine_modules: module name and file path resolution
# ---------------------------------------------------------------------------


class TestRegisterEngineModules:
    """_register_engine_modules must register the correct module names."""

    def test_parakeet_registers_correct_names(self) -> None:
        module = _load_runner_module(_PARAKEET_RUNNER_PATH, "parakeet_runner_reg")

        with patch("importlib.util.spec_from_file_location") as mock_spec:
            fake_spec = MagicMock()
            fake_spec.loader = MagicMock()
            mock_spec.return_value = fake_spec

            module._register_engine_modules()

            registered_names = [c[0][0] for c in mock_spec.call_args_list]

        assert "engines.stt_transcribe_parakeet" in registered_names
        assert "engines.stt_rt_parakeet" in registered_names

    def test_parakeet_onnx_registers_correct_names(self) -> None:
        module = _load_runner_module(
            _PARAKEET_ONNX_RUNNER_PATH, "parakeet_onnx_runner_reg"
        )

        with patch("importlib.util.spec_from_file_location") as mock_spec:
            fake_spec = MagicMock()
            fake_spec.loader = MagicMock()
            mock_spec.return_value = fake_spec

            module._register_engine_modules()

            registered_names = [c[0][0] for c in mock_spec.call_args_list]

        assert "engines.stt_transcribe_parakeet_onnx" in registered_names
        assert "engines.stt_rt_parakeet_onnx" in registered_names

    def test_parakeet_batch_engine_path_exists(self) -> None:
        """The batch engine file referenced by _register_engine_modules exists."""
        engines_root = _PARAKEET_RUNNER_PATH.resolve().parents[2]
        batch_path = engines_root / "stt-transcribe" / "parakeet" / "engine.py"
        assert batch_path.exists(), f"Batch engine not found: {batch_path}"

    def test_parakeet_rt_engine_path_exists(self) -> None:
        """The RT engine file referenced by _register_engine_modules exists."""
        engines_root = _PARAKEET_RUNNER_PATH.resolve().parents[2]
        rt_path = engines_root / "stt-rt" / "parakeet" / "engine.py"
        assert rt_path.exists(), f"RT engine not found: {rt_path}"

    def test_parakeet_onnx_batch_engine_path_exists(self) -> None:
        """The batch ONNX engine file referenced by _register_engine_modules exists."""
        engines_root = _PARAKEET_ONNX_RUNNER_PATH.resolve().parents[2]
        batch_path = engines_root / "stt-transcribe" / "parakeet-onnx" / "engine.py"
        assert batch_path.exists(), f"Batch ONNX engine not found: {batch_path}"

    def test_parakeet_onnx_rt_engine_path_exists(self) -> None:
        """The RT ONNX engine file referenced by _register_engine_modules exists."""
        engines_root = _PARAKEET_ONNX_RUNNER_PATH.resolve().parents[2]
        rt_path = engines_root / "stt-rt" / "parakeet-onnx" / "engine.py"
        assert rt_path.exists(), f"RT ONNX engine not found: {rt_path}"
