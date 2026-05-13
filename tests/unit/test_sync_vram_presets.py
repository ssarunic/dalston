"""Tests for M89.3 ``sync_vram_presets``: load profiles, derive budgets,
rewrite the ``vram_budget_by_gpu`` literal in ``dalston-aws``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from dalston.tools.sync_vram_presets import (
    ProfileData,
    apply_rewrites,
    build_engine_id_to_preset_key,
    derive_budgets,
    load_profiles,
    main,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
LIVE_DALSTON_AWS = REPO_ROOT / "infra" / "scripts" / "dalston-aws"


# ---------------------------------------------------------------------------
# build_engine_id_to_preset_key — runs against the real dalston-aws
# ---------------------------------------------------------------------------


def test_engine_id_to_preset_key_against_real_script() -> None:
    mapping = build_engine_id_to_preset_key(LIVE_DALSTON_AWS)
    # The interesting case is pyannote: preset key "pyannote" but engine_id
    # "pyannote-4.0" — the sync tool MUST translate or it would write
    # cells the runtime resolver can't find.
    assert mapping["pyannote-4.0"] == "pyannote"
    assert mapping["nemo"] == "nemo"
    assert mapping["onnx"] == "onnx"
    assert mapping["faster-whisper"] == "faster-whisper"


# ---------------------------------------------------------------------------
# load_profiles — schema validation + skip messages
# ---------------------------------------------------------------------------


def _write_profile(path: Path, **overrides: Any) -> Path:
    base: dict[str, Any] = {
        "schema_version": "1.0",
        "engine_id": "nemo",
        "model_id": "nvidia/parakeet-tdt-0.6b-v3",
        "stage": "transcribe",
        "gpu": "Tesla T4",
        "gpu_vram_mb": 15360,
        "cuda_overhead_mb": 700,
        "measurements": [],
        "model": {
            "weights_mb": 700,
            "formula": "S",
            "coefficients": {},
            "r_squared": 1.0,
            "safety_margin": 0.15,
        },
        "recommended_budget_mb": {"solo": 12000},
        "baselines": {"solo": {"start_mb": 5200}},
    }
    base.update(overrides)
    path.write_text(json.dumps(base, indent=2))
    return path


def test_load_profiles_resolves_engine_id_and_normalises_gpu(tmp_path: Path) -> None:
    _write_profile(tmp_path / "transcribe-nemo-T4.json")
    profiles, skipped = load_profiles(
        tmp_path, {"nemo": "nemo", "pyannote-4.0": "pyannote"}
    )
    assert skipped == []
    assert len(profiles) == 1
    p = profiles[0]
    assert p.preset_key == "nemo"
    assert p.gpu_normalized == "T4"
    assert p.recommended_budget_mb == {"solo": 12000}
    assert p.baselines_start_mb == {"solo": 5200}


def test_load_profiles_skips_missing_recommended_budget(tmp_path: Path) -> None:
    # Older M84-era profile without recommended_budget_mb / baselines.
    _write_profile(
        tmp_path / "transcribe-nemo-T4.json",
        recommended_budget_mb=None,
        baselines=None,
    )
    profiles, skipped = load_profiles(tmp_path, {"nemo": "nemo"})
    assert profiles == []
    assert any("recommended_budget_mb" in m for m in skipped)


def test_load_profiles_skips_unknown_engine_id(tmp_path: Path) -> None:
    _write_profile(tmp_path / "transcribe-mystery-T4.json", engine_id="mystery")
    profiles, skipped = load_profiles(tmp_path, {"nemo": "nemo"})
    assert profiles == []
    assert any("mystery" in m and "no matching" in m for m in skipped)


def test_load_profiles_skips_unknown_gpu(tmp_path: Path) -> None:
    _write_profile(tmp_path / "transcribe-nemo-Mars.json", gpu="Martian GPU")
    profiles, skipped = load_profiles(tmp_path, {"nemo": "nemo"})
    assert profiles == []
    assert any("Martian GPU" in m for m in skipped)


def test_load_profiles_handles_corrupt_json(tmp_path: Path) -> None:
    (tmp_path / "broken.json").write_text("not json {")
    profiles, skipped = load_profiles(tmp_path, {"nemo": "nemo"})
    assert profiles == []
    assert skipped and "cannot parse" in skipped[0]


# ---------------------------------------------------------------------------
# derive_budgets — solo direct, coloc subtraction, conflicts
# ---------------------------------------------------------------------------


def _profile(
    *,
    preset_key: str,
    gpu: str,
    rec: dict[str, int],
    baselines: dict[str, int] | None = None,
    path: Path = Path("synth.json"),
    engine_id: str = "synth",
) -> ProfileData:
    return ProfileData(
        path=path,
        engine_id=engine_id,
        preset_key=preset_key,
        gpu_normalized=gpu,
        recommended_budget_mb=rec,
        baselines_start_mb=baselines or {},
    )


def test_derive_solo_only() -> None:
    profiles = [
        _profile(
            preset_key="nemo", gpu="T4", rec={"solo": 12000}, baselines={"solo": 5200}
        ),
        _profile(
            preset_key="pyannote",
            gpu="T4",
            rec={"solo": 3000},
            baselines={"solo": 1400},
        ),
    ]
    out = derive_budgets(profiles)
    assert out.conflicts == []
    assert out.budgets["nemo"]["T4"]["solo"] == 12000
    assert out.budgets["pyannote"]["T4"]["solo"] == 3000


def test_derive_coloc_subtracts_other_solo_baseline() -> None:
    # nemo on T4: coloc-mode peak budget = 14000 (includes pyannote's 1400 MB weights)
    # pyannote's solo baseline = 1400 (subject_alone_mb)
    # Subject_only = 14000 - 1400 = 12600, + 500 headroom = 13100, round up = 14000.
    profiles = [
        _profile(
            preset_key="nemo",
            gpu="T4",
            rec={"solo": 12000, "coloc_with_pyannote": 14000},
            baselines={"solo": 5200, "coloc_with_pyannote": 6600},
        ),
        _profile(
            preset_key="pyannote",
            gpu="T4",
            rec={"solo": 3000, "coloc_with_nemo": 9000},
            baselines={"solo": 1400, "coloc_with_nemo": 6600},
        ),
    ]
    out = derive_budgets(profiles, headroom_mb=500)
    assert out.budgets["nemo"]["T4"]["coloc_with_pyannote"] == 14000
    # pyannote coloc: 9000 - 5200 (nemo solo baseline) + 500 = 4300, round up = 5000.
    assert out.budgets["pyannote"]["T4"]["coloc_with_nemo"] == 5000


def test_derive_coloc_skipped_when_other_solo_baseline_missing() -> None:
    profiles = [
        _profile(
            preset_key="nemo",
            gpu="T4",
            rec={"solo": 12000, "coloc_with_pyannote": 14000},
            baselines={"solo": 5200},
        ),
        # pyannote profile exists but has no solo baseline (e.g. only coloc was run).
        _profile(
            preset_key="pyannote",
            gpu="T4",
            rec={"coloc_with_nemo": 9000},
            baselines={"coloc_with_nemo": 6600},
        ),
    ]
    out = derive_budgets(profiles)
    # nemo coloc is omitted (no pyannote solo baseline to subtract).
    assert "coloc_with_pyannote" not in out.budgets["nemo"]["T4"]
    # But solo is fine.
    assert out.budgets["nemo"]["T4"]["solo"] == 12000
    assert any("no solo baseline" in n for n in out.notes)


def test_derive_conflict_between_profiles() -> None:
    pa = Path("a.json")
    pb = Path("b.json")
    profiles = [
        _profile(
            preset_key="nemo",
            gpu="T4",
            rec={"solo": 12000},
            baselines={"solo": 5200},
            path=pa,
        ),
        _profile(
            preset_key="nemo",
            gpu="T4",
            rec={"solo": 13000},
            baselines={"solo": 5200},
            path=pb,
        ),
    ]
    out = derive_budgets(profiles)
    assert out.budgets.get("nemo", {}).get("T4", {}).get("solo") is None
    assert len(out.conflicts) == 1
    c = out.conflicts[0]
    assert {c.value_a, c.value_b} == {12000, 13000}


# ---------------------------------------------------------------------------
# apply_rewrites — round-trip + idempotency
# ---------------------------------------------------------------------------


_MINI_SCRIPT = '''\
"""Tiny stand-in for dalston-aws that just defines GPU_ENGINE_PRESETS."""
from typing import Any

GPU_ENGINE_PRESETS: dict[str, dict] = {
    "nemo": {
        "image": "stt-transcribe-nemo",
        "engine_id": "nemo",
        "container": "stt-transcribe-nemo",
        "extra_env": {
            "DALSTON_VRAM_BUDGET_MB": "20000",
        },
        "vram_budget_by_gpu": {
            "T4": {"solo": 11000, "coloc_with_pyannote": 9000},
            "A10G": {"solo": 20000, "coloc_with_pyannote": 18000},
            "L4": {"solo": 20000, "coloc_with_pyannote": 20000},
        },
    },
    "pyannote": {
        "image": "stt-diarize-pyannote",
        "engine_id": "pyannote-4.0",
        "container": "stt-diarize-pyannote-4-0",
        "extra_env": {
            "DALSTON_VRAM_BUDGET_MB": "4000",
        },
        "vram_budget_by_gpu": {
            "T4": {"solo": 3500, "coloc_with_nemo": 3000},
            "A10G": {"solo": 4000, "coloc_with_nemo": 4000},
            "L4": {"solo": 4000, "coloc_with_nemo": 4000},
        },
    },
}
'''


def test_apply_rewrites_updates_existing_budgets() -> None:
    derived = {
        "nemo": {
            "T4": {"solo": 12000, "coloc_with_pyannote": 10000},
        },
    }
    rewritten = apply_rewrites(_MINI_SCRIPT, derived)
    # New nemo T4 numbers landed.
    assert '"T4": {"solo": 12000, "coloc_with_pyannote": 10000}' in rewritten
    # Untouched cells preserved.
    assert '"A10G": {"solo": 20000, "coloc_with_pyannote": 18000}' in rewritten
    # Pyannote left alone (not in derived).
    assert '"T4": {"solo": 3500, "coloc_with_nemo": 3000}' in rewritten


def test_apply_rewrites_is_idempotent() -> None:
    derived = {
        "nemo": {
            "T4": {"solo": 11000, "coloc_with_pyannote": 9000},
            "A10G": {"solo": 20000, "coloc_with_pyannote": 18000},
            "L4": {"solo": 20000, "coloc_with_pyannote": 20000},
        },
    }
    once = apply_rewrites(_MINI_SCRIPT, derived)
    twice = apply_rewrites(once, derived)
    assert once == twice


def test_apply_rewrites_skips_preset_without_existing_field() -> None:
    # An engine that doesn't yet declare vram_budget_by_gpu must NOT have one
    # injected by the sync tool — keeping the schema migration explicit.
    minimal = """\
GPU_ENGINE_PRESETS: dict[str, dict] = {
    "onnx": {
        "engine_id": "onnx",
        "extra_env": {},
    },
}
"""
    rewritten = apply_rewrites(minimal, {"onnx": {"T4": {"solo": 10000}}})
    assert rewritten == minimal


def test_apply_rewrites_handles_multiple_presets_in_one_pass() -> None:
    derived = {
        "nemo": {"T4": {"solo": 12000}},
        "pyannote": {"T4": {"solo": 3500}},
    }
    rewritten = apply_rewrites(_MINI_SCRIPT, derived)
    assert '"T4": {"solo": 12000}' in rewritten
    assert '"T4": {"solo": 3500}' in rewritten
    # GPUs NOT covered by ``derived`` are preserved from the existing map.
    # Without this, running the sync with partial coverage would silently
    # erase hand-seeded values for un-calibrated GPUs.
    assert "A10G" in rewritten
    assert "L4" in rewritten


# ---------------------------------------------------------------------------
# main() — end-to-end against the real dalston-aws (dry-run)
# ---------------------------------------------------------------------------


def test_main_dry_run_with_no_profiles_returns_zero(tmp_path: Path) -> None:
    rc = main(
        [
            "--profiles-dir",
            str(tmp_path),
            "--target",
            str(LIVE_DALSTON_AWS),
            "--dry-run",
        ]
    )
    assert rc == 0


def test_main_refuses_on_conflicts(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_profile(
        tmp_path / "transcribe-nemo-T4.json",
        recommended_budget_mb={"solo": 12000},
        baselines={"solo": {"start_mb": 5200}},
    )
    _write_profile(
        tmp_path / "transcribe-nemo-T4-alt.json",
        recommended_budget_mb={"solo": 14000},
        baselines={"solo": {"start_mb": 5200}},
    )
    rc = main(
        [
            "--profiles-dir",
            str(tmp_path),
            "--target",
            str(LIVE_DALSTON_AWS),
            "--dry-run",
        ]
    )
    assert rc == 2
    err = capsys.readouterr().err
    assert "conflict" in err
