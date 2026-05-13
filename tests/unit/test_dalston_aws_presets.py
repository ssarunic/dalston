"""Tests for M89.1 per-GPU VRAM budget resolution in dalston-aws.

The dalston-aws script lives outside the python package as an executable
without a ``.py`` suffix. Tests import it via ``importlib.util`` and exercise
the two new functions (``_resolve_vram_budget``, ``_generate_docker_run_block``)
plus the existing override hook.
"""

from __future__ import annotations

import importlib.util
import sys
from importlib.machinery import SourceFileLoader
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "infra" / "scripts" / "dalston-aws"


def _load_dalston_aws():
    """Load infra/scripts/dalston-aws as a module without invoking ``main()``.

    The script has no ``.py`` suffix, so ``spec_from_file_location`` rejects
    it without an explicit ``SourceFileLoader``. The script guards ``main()``
    behind ``if __name__ == "__main__"``, so importing under a synthetic
    name is side-effect-free apart from the module-level constants and
    function definitions we want to test.
    """
    loader = SourceFileLoader("dalston_aws_under_test", str(SCRIPT_PATH))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec and spec.loader, f"could not load {SCRIPT_PATH}"
    module = importlib.util.module_from_spec(spec)
    sys.modules["dalston_aws_under_test"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def daws():
    return _load_dalston_aws()


# ---------------------------------------------------------------------------
# _resolve_vram_budget — direct table lookup
# ---------------------------------------------------------------------------


def test_t4_coloc_nemo_pyannote(daws) -> None:
    assert (
        daws._resolve_vram_budget("nemo", "g4dn.xlarge", ["nemo", "pyannote"]) == 10000
    )
    assert (
        daws._resolve_vram_budget("pyannote", "g4dn.xlarge", ["nemo", "pyannote"])
        == 2000
    )


def test_t4_solo(daws) -> None:
    assert daws._resolve_vram_budget("nemo", "g4dn.xlarge", ["nemo"]) == 11000
    assert daws._resolve_vram_budget("pyannote", "g4dn.xlarge", ["pyannote"]) == 3500


def test_a10g_coloc(daws) -> None:
    assert daws._resolve_vram_budget("nemo", "g5.xlarge", ["nemo", "pyannote"]) == 18000


def test_l4_coloc(daws) -> None:
    # L4 coloc gives nemo its full 20 GB budget; pyannote stays at 4 GB.
    assert daws._resolve_vram_budget("nemo", "g6.xlarge", ["nemo", "pyannote"]) == 20000
    assert (
        daws._resolve_vram_budget("pyannote", "g6.xlarge", ["nemo", "pyannote"]) == 4000
    )


def test_unknown_gpu_returns_none(daws) -> None:
    # g4ad is AMD — out of scope for M89.1. Resolver returns None so the
    # caller falls back to the static DALSTON_VRAM_BUDGET_MB in extra_env.
    assert (
        daws._resolve_vram_budget("nemo", "g4ad.xlarge", ["nemo", "pyannote"]) is None
    )


def test_engine_without_budget_map_returns_none(daws) -> None:
    # ONNX preset has no vram_budget_by_gpu yet; resolver should no-op.
    assert daws._resolve_vram_budget("onnx", "g4dn.xlarge", ["onnx"]) is None


# ---------------------------------------------------------------------------
# _generate_docker_run_block — end-to-end env rendering
# ---------------------------------------------------------------------------


def _budget_in(block: str) -> str | None:
    """Extract the rendered DALSTON_VRAM_BUDGET_MB value from a docker-run block."""
    marker = 'DALSTON_VRAM_BUDGET_MB="'
    idx = block.find(marker)
    if idx == -1:
        return None
    start = idx + len(marker)
    end = block.index('"', start)
    return block[start:end]


def test_docker_block_t4_coloc(daws, monkeypatch) -> None:
    monkeypatch.setenv("HF_TOKEN", "hf_dummy")
    block = daws._generate_docker_run_block(
        "nemo", 9100, gpu_type="g4dn.xlarge", co_engines=["nemo", "pyannote"]
    )
    assert _budget_in(block) == "10000"


def test_docker_block_no_gpu_type_falls_back_to_preset(daws, monkeypatch) -> None:
    monkeypatch.setenv("HF_TOKEN", "hf_dummy")
    # Called without gpu_type — preserves legacy behaviour: the preset's
    # static DALSTON_VRAM_BUDGET_MB (20000 for nemo) is rendered as-is.
    block = daws._generate_docker_run_block("nemo", 9100)
    assert _budget_in(block) == "20000"


def test_docker_block_unknown_gpu_falls_back_to_preset(daws, monkeypatch) -> None:
    monkeypatch.setenv("HF_TOKEN", "hf_dummy")
    block = daws._generate_docker_run_block(
        "nemo", 9100, gpu_type="g4ad.xlarge", co_engines=["nemo"]
    )
    # g4ad is unknown → resolver returns None → preset fallback (20000) kept.
    assert _budget_in(block) == "20000"


def test_override_env_var_wins_over_per_gpu_budget(daws, monkeypatch) -> None:
    monkeypatch.setenv("HF_TOKEN", "hf_dummy")
    monkeypatch.setenv("DALSTON_OVERRIDE__nemo__VRAM_BUDGET_MB", "7000")
    block = daws._generate_docker_run_block(
        "nemo", 9100, gpu_type="g4dn.xlarge", co_engines=["nemo", "pyannote"]
    )
    # Override (7000) must beat the T4-coloc default (10000).
    assert _budget_in(block) == "7000"
