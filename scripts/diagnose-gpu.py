#!/usr/bin/env python3
"""GPU diagnostic script for ONNX engine containers.

Run inside a running container to diagnose GPU/CUDA issues:
    docker exec stt-unified-onnx python /app/scripts/diagnose-gpu.py

Or copy into container first:
    docker cp scripts/diagnose-gpu.py stt-unified-onnx:/tmp/
    docker exec stt-unified-onnx python /tmp/diagnose-gpu.py
"""

import subprocess
import sys
import time


def section(title: str) -> None:
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print(f"{'=' * 60}\n")


def check_packages() -> None:
    section("1. Installed ONNX Packages")
    result = subprocess.run(
        [sys.executable, "-m", "pip", "list", "--format=columns"],
        capture_output=True,
        text=True,
    )
    for line in result.stdout.splitlines():
        if "onnx" in line.lower():
            print(f"  {line}")

    # Check for the conflict
    has_cpu = False
    has_gpu = False
    for line in result.stdout.splitlines():
        low = line.lower()
        if "onnxruntime-gpu" in low:
            has_gpu = True
        elif "onnxruntime " in low or low.startswith("onnxruntime "):
            has_cpu = True

    if has_cpu and has_gpu:
        print(
            "\n  *** CONFLICT DETECTED: both onnxruntime AND onnxruntime-gpu installed! ***"
        )
        print("  This causes CUDA EP to silently fall back to CPU.")
        print("  Fix: pip uninstall onnxruntime && pip install onnxruntime-gpu")
    elif has_gpu:
        print("\n  OK: only onnxruntime-gpu installed (correct for GPU)")
    elif has_cpu:
        print("\n  WARNING: only onnxruntime (CPU) installed, no GPU support")
    else:
        print("\n  ERROR: no onnxruntime package found")


def check_providers() -> None:
    section("2. ONNX Runtime Providers")
    try:
        import onnxruntime as ort

        print(f"  Version:  {ort.__version__}")
        print(f"  File:     {ort.__file__}")
        providers = ort.get_available_providers()
        print(f"  Available providers: {providers}")

        if "CUDAExecutionProvider" in providers:
            print(
                "  CUDA EP is registered (but may not actually work - see test below)"
            )
        else:
            print("  WARNING: CUDAExecutionProvider NOT available")
            print("  Only CPU inference is possible.")
    except ImportError:
        print("  ERROR: cannot import onnxruntime")


def check_cuda_libs() -> None:
    section("3. CUDA Shared Libraries")
    try:
        import os

        import onnxruntime as ort

        ort_dir = os.path.dirname(ort.__file__)
        capi_dir = os.path.join(ort_dir, "capi")

        # Check for CUDA-related .so files
        for search_dir in [ort_dir, capi_dir]:
            if not os.path.isdir(search_dir):
                continue
            for f in sorted(os.listdir(search_dir)):
                if f.endswith(".so") and (
                    "cuda" in f.lower()
                    or "cudnn" in f.lower()
                    or "onnxruntime" in f.lower()
                ):
                    full = os.path.join(search_dir, f)
                    size_mb = os.path.getsize(full) / (1024 * 1024)
                    print(f"  {f:50s} {size_mb:8.1f} MB")
    except Exception as e:
        print(f"  Error scanning libraries: {e}")


def check_gpu_compute() -> None:
    section("4. GPU Compute Test (matmul via ONNX Runtime)")
    try:
        import numpy as np
        import onnxruntime as ort

        if "CUDAExecutionProvider" not in ort.get_available_providers():
            print("  SKIPPED: CUDAExecutionProvider not available")
            return

        # Create a simple matmul ONNX model in memory
        from onnx import TensorProto, helper

        A = helper.make_tensor_value_info("A", TensorProto.FLOAT, [256, 256])
        B = helper.make_tensor_value_info("B", TensorProto.FLOAT, [256, 256])
        C = helper.make_tensor_value_info("C", TensorProto.FLOAT, [256, 256])
        node = helper.make_node("MatMul", ["A", "B"], ["C"])
        graph = helper.make_graph([node], "matmul_test", [A, B], [C])
        model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 13)])

        model_bytes = model.SerializeToString()

        # Run on CUDA
        sess_opts = ort.SessionOptions()
        sess_opts.log_severity_level = 1  # verbose
        sess = ort.InferenceSession(
            model_bytes,
            sess_options=sess_opts,
            providers=["CUDAExecutionProvider"],
        )

        active_providers = sess.get_providers()
        print(f"  Session providers: {active_providers}")

        if active_providers[0] != "CUDAExecutionProvider":
            print("  *** CUDA EP NOT ACTIVE despite being requested! ***")
            print("  This confirms GPU compute is broken.")
            return

        a = np.random.randn(256, 256).astype(np.float32)
        b = np.random.randn(256, 256).astype(np.float32)

        # Warmup
        sess.run(["C"], {"A": a, "B": b})

        # Benchmark
        n_iters = 100
        start = time.perf_counter()
        for _ in range(n_iters):
            sess.run(["C"], {"A": a, "B": b})
        elapsed = time.perf_counter() - start

        print(
            f"  {n_iters} matmuls in {elapsed:.3f}s ({elapsed / n_iters * 1000:.1f}ms/iter)"
        )
        print(
            "  GPU compute is WORKING"
            if elapsed < 2.0
            else "  WARNING: suspiciously slow"
        )

    except ImportError:
        print("  SKIPPED: 'onnx' package not installed (needed to build test model)")
        print("  Install with: pip install onnx")
    except Exception as e:
        print(f"  ERROR: {e}")
        import traceback

        traceback.print_exc()


def check_actual_model_session() -> None:
    section("5. Real Model Provider Placement")
    try:
        import onnxruntime as ort

        if "CUDAExecutionProvider" not in ort.get_available_providers():
            print("  SKIPPED: no CUDA EP")
            return

        # Try to load the actual encoder model if cached
        import glob

        encoder_files = glob.glob("/models/**/encoder*model*.onnx", recursive=True)
        if not encoder_files:
            encoder_files = glob.glob(
                "/root/.cache/**/encoder*model*.onnx", recursive=True
            )

        if not encoder_files:
            print("  No cached encoder model found, skipping real model test")
            return

        encoder_path = encoder_files[0]
        print(f"  Testing with: {encoder_path}")

        sess_opts = ort.SessionOptions()
        sess_opts.log_severity_level = 1

        sess = ort.InferenceSession(
            encoder_path,
            sess_options=sess_opts,
            providers=["CUDAExecutionProvider", "CPUExecutionProvider"],
        )

        print(f"  Active providers: {sess.get_providers()}")
        print(f"  Provider options: {sess.get_provider_options()}")

    except Exception as e:
        print(f"  ERROR: {e}")


if __name__ == "__main__":
    print("Dalston ONNX Engine GPU Diagnostic")
    print(f"Python: {sys.version}")

    check_packages()
    check_providers()
    check_cuda_libs()
    check_gpu_compute()
    check_actual_model_session()

    section("Done")
    print("  Copy output above and share for analysis.")
