"""Tests for GPU OOM detection, binary backoff, and safe batch caching."""

from dalston.engine_sdk.inference.gpu_guard import clear_gpu_cache, is_oom_error
from dalston.engine_sdk.vram_budget import AdaptiveVRAMParams, EngineVRAMParams

# ---------------------------------------------------------------------------
# is_oom_error detection
# ---------------------------------------------------------------------------


class TestIsOomError:
    """Verify OOM detection across PyTorch and ONNX Runtime error patterns."""

    def test_pytorch_cuda_oom(self):
        exc = RuntimeError(
            "CUDA out of memory. Tried to allocate 22.00 GiB "
            "(GPU 0; 15.00 GiB total capacity)"
        )
        assert is_oom_error(exc)

    def test_onnx_runtime_allocation_failure(self):
        exc = RuntimeError(
            "[ONNXRuntimeError] : 6 : RUNTIME_EXCEPTION : "
            "Failed to allocate memory for requested buffer of size 22609920000"
        )
        assert is_oom_error(exc)

    def test_onnx_bfc_arena(self):
        exc = RuntimeError(
            "onnxruntime/core/framework/bfc_arena.cc:358 "
            "Failed to allocate memory for requested buffer"
        )
        assert is_oom_error(exc)

    def test_torch_out_of_memory_error_type(self):
        """torch.cuda.OutOfMemoryError inherits from RuntimeError."""

        class OutOfMemoryError(RuntimeError):
            pass

        exc = OutOfMemoryError("CUDA out of memory")
        assert is_oom_error(exc)

    def test_unrelated_runtime_error(self):
        exc = RuntimeError("Model file not found: /models/foo.onnx")
        assert not is_oom_error(exc)

    def test_value_error_not_oom(self):
        exc = ValueError("Invalid batch size: -1")
        assert not is_oom_error(exc)

    def test_generic_allocate_without_gpu_context(self):
        exc = RuntimeError("Failed to allocate buffer for CPU tensor")
        assert not is_oom_error(exc)


# ---------------------------------------------------------------------------
# AdaptiveVRAMParams.update_safe_batch_size
# ---------------------------------------------------------------------------


class TestUpdateSafeBatchSize:
    """Verify batch size caching after OOM backoff."""

    def _make_params(self, solo_batch: int = 144) -> AdaptiveVRAMParams:
        return AdaptiveVRAMParams(
            solo=EngineVRAMParams(vad_batch_size=solo_batch),
            concurrent=EngineVRAMParams(vad_batch_size=solo_batch),
            budget_mb=15360,
            profile_source="calibrated",
        )

    def test_reduces_batch_size(self):
        params = self._make_params(144)
        params.update_safe_batch_size(16)
        assert params.solo.vad_batch_size == 16

    def test_does_not_increase(self):
        params = self._make_params(8)
        params.update_safe_batch_size(16)
        assert params.solo.vad_batch_size == 8

    def test_no_op_when_equal(self):
        params = self._make_params(16)
        params.update_safe_batch_size(16)
        assert params.solo.vad_batch_size == 16

    def test_select_returns_updated_value(self):
        params = self._make_params(144)
        params.update_safe_batch_size(16)
        selected = params.select(queue_depth=0)
        assert selected.vad_batch_size == 16


# ---------------------------------------------------------------------------
# clear_gpu_cache (smoke test, no GPU needed)
# ---------------------------------------------------------------------------


class TestClearGpuCache:
    def test_does_not_raise_without_gpu(self):
        """Should be a no-op when no GPU is available."""
        clear_gpu_cache()
