"""Guardrail tests to prevent GPU-specific packages from leaking into shared extras.

Background: onnxruntime (CPU) and onnxruntime-gpu install into the same Python
namespace. If a shared pyproject.toml extra (like realtime-sdk) pulls in the CPU
variant, every GPU engine Dockerfile that installs that extra will have its GPU
runtime files overwritten — causing silent fallback to CPU with no errors.

These tests ensure this class of bug cannot be reintroduced.
"""

import tomllib
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Packages that have GPU/CPU variants sharing the same Python namespace.
# Adding a CPU variant to a shared extra will break GPU engines.
GPU_CONFLICTING_PACKAGES = {
    "onnxruntime",  # conflicts with onnxruntime-gpu
    "onnxruntime-gpu",  # should never be in shared extras
    "torch",  # conflicts with torch+cuda index
    "torchaudio",  # same — must use GPU index
    "torchvision",  # same
    "ctranslate2",  # has CPU/CUDA variants
}

# Extras that are installed across multiple engine types (both CPU and GPU).
# These must NOT contain GPU-conflicting packages.
SHARED_EXTRAS = {
    "engine-sdk",
    "realtime-sdk",
    "gateway",
    "orchestrator",
    "session-router",
}


@pytest.fixture()
def pyproject() -> dict:
    with open(PROJECT_ROOT / "pyproject.toml", "rb") as f:
        return tomllib.load(f)


class TestSharedExtrasNoGpuPackages:
    """Verify shared pyproject.toml extras don't contain GPU-conflicting packages."""

    def test_no_gpu_conflicting_packages_in_shared_extras(self, pyproject: dict):
        """Shared extras must not include packages that conflict with GPU variants.

        If you need onnxruntime, torch, etc., add them to the engine's own
        requirements.txt or Dockerfile instead of a shared pyproject.toml extra.
        """
        extras = pyproject.get("project", {}).get("optional-dependencies", {})
        violations = []

        for extra_name in SHARED_EXTRAS:
            if extra_name not in extras:
                continue
            for dep in extras[extra_name]:
                # Extract package name (before any version specifier or extras)
                pkg_name = (
                    dep.split("[")[0]
                    .split(">")[0]
                    .split("<")[0]
                    .split("=")[0]
                    .split(";")[0]
                    .strip()
                    .lower()
                )
                if pkg_name in GPU_CONFLICTING_PACKAGES:
                    violations.append(f"  {extra_name}: {dep}")

        assert not violations, (
            "GPU-conflicting packages found in shared extras!\n"
            "These packages have CPU/GPU variants that share a Python namespace.\n"
            "Adding them to shared extras breaks GPU engines silently.\n"
            "Move them to the engine's own requirements.txt instead.\n\n"
            "Violations:\n" + "\n".join(violations)
        )

    def test_gpu_conflicting_list_is_not_empty(self):
        """Sanity check that our blocklist isn't accidentally empty."""
        assert len(GPU_CONFLICTING_PACKAGES) >= 3


class TestRealtimeSdkVadGracefulDegradation:
    """Verify VAD module handles missing onnxruntime without import-time errors."""

    def test_vad_module_imports_without_onnxruntime(self):
        """The realtime_sdk.vad module must import cleanly even without onnxruntime.

        onnxruntime is lazy-imported at model load time, not at import time.
        This allows non-ONNX engines to use realtime-sdk without pulling in
        onnxruntime as a dependency.
        """
        # This import should succeed regardless of whether onnxruntime is installed
        from dalston.realtime_sdk import vad  # noqa: F401
