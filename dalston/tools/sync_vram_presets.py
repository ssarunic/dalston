"""M89.3: rewrite `vram_budget_by_gpu` in `infra/scripts/dalston-aws`.

Reads every profile JSON in ``--profiles-dir`` (default
``dalston/tools/vram_profiles/``), pulls out ``recommended_budget_mb`` and
``baselines`` for each ``(engine, gpu)`` shape, performs the cross-profile
coloc subtraction (subject's coloc budget = subject's coloc-mode
``recommended_budget_mb`` minus the *other* engine's solo
``baselines.start_mb``), and rewrites the ``vram_budget_by_gpu`` literal
inside each affected ``GPU_ENGINE_PRESETS`` entry in
``infra/scripts/dalston-aws``.

Usage::

    python -m dalston.tools.sync_vram_presets               # write
    python -m dalston.tools.sync_vram_presets --dry-run     # print diff only

Idempotent: re-running with unchanged profiles produces no diff.
"""

from __future__ import annotations

import argparse
import ast
import difflib
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PROFILES_DIR = REPO_ROOT / "dalston" / "tools" / "vram_profiles"
DEFAULT_TARGET = REPO_ROOT / "infra" / "scripts" / "dalston-aws"

# Map GPU names produced by `nvidia-smi --query-gpu=name` to the keys
# `_resolve_vram_budget` uses (GPU_FAMILY_TO_NAME values in dalston-aws).
# Profiles record `gpu` verbatim from `pynvml.nvmlDeviceGetName`, so we
# accept a few common spellings.
_GPU_NAME_NORMALIZE: dict[str, str] = {
    "T4": "T4",
    "Tesla T4": "T4",
    "A10G": "A10G",
    "NVIDIA A10G": "A10G",
    "L4": "L4",
    "NVIDIA L4": "L4",
    "L40S": "L40S",
    "NVIDIA L40S": "L40S",
    "A100": "A100",
    "NVIDIA A100": "A100",
    "H100": "H100",
    "NVIDIA H100": "H100",
    "H200": "H200",
    "NVIDIA H200": "H200",
}

_COLOC_PREFIX = "coloc_with_"


# ---------------------------------------------------------------------------
# Profile loading
# ---------------------------------------------------------------------------


@dataclass
class ProfileData:
    """Subset of a profile JSON that the sync tool consumes."""

    path: Path
    engine_id: str
    preset_key: str
    gpu_normalized: str
    # mode_key (e.g. "solo", "coloc_with_pyannote") -> budget MB
    recommended_budget_mb: dict[str, int] = field(default_factory=dict)
    # mode_key -> baseline_at_start MB (subject_alone for solo, both_idle for coloc)
    baselines_start_mb: dict[str, int] = field(default_factory=dict)


def _normalize_gpu(raw: str) -> str | None:
    """Return canonical GPU key (T4, A10G, ...) or None for unknown."""
    if not raw:
        return None
    return _GPU_NAME_NORMALIZE.get(raw.strip())


def load_profiles(
    profiles_dir: Path, engine_id_to_preset_key: dict[str, str]
) -> tuple[list[ProfileData], list[str]]:
    """Load every ``*.json`` in ``profiles_dir``.

    Returns ``(profiles, skipped_messages)``. Profiles missing required
    M89.2 fields (``recommended_budget_mb``, ``baselines``,
    ``engine_id``, ``gpu``) are skipped with a message — they're
    typically older M84-era profiles that pre-date the calibrator changes.
    """
    profiles: list[ProfileData] = []
    skipped: list[str] = []

    if not profiles_dir.exists():
        return profiles, [f"profiles dir {profiles_dir} does not exist"]

    for path in sorted(profiles_dir.glob("*.json")):
        try:
            raw = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            skipped.append(f"{path.name}: cannot parse JSON ({exc})")
            continue
        if not isinstance(raw, dict):
            skipped.append(f"{path.name}: not a JSON object")
            continue

        engine_id = raw.get("engine_id")
        gpu_raw = raw.get("gpu")
        budgets = raw.get("recommended_budget_mb")
        baselines = raw.get("baselines")

        if not engine_id or not gpu_raw:
            skipped.append(f"{path.name}: missing engine_id or gpu")
            continue
        if not isinstance(budgets, dict) or not isinstance(baselines, dict):
            skipped.append(
                f"{path.name}: missing recommended_budget_mb / baselines "
                f"(run calibrator with --throughput-sweep to add them)"
            )
            continue

        preset_key = engine_id_to_preset_key.get(engine_id)
        if not preset_key:
            skipped.append(
                f"{path.name}: engine_id {engine_id!r} has no matching entry "
                f"in GPU_ENGINE_PRESETS (add the engine to the preset map "
                f"before syncing its profile)"
            )
            continue

        gpu = _normalize_gpu(gpu_raw)
        if not gpu:
            skipped.append(
                f"{path.name}: gpu {gpu_raw!r} doesn't normalize to a known "
                f"GPU key (T4, A10G, L4, L40S, A100, H100, H200)"
            )
            continue

        rec: dict[str, int] = {}
        bsl: dict[str, int] = {}
        for mode_key, value in budgets.items():
            if isinstance(value, int) and value > 0:
                rec[mode_key] = value
        for mode_key, bdata in baselines.items():
            if not isinstance(bdata, dict):
                continue
            start = bdata.get("start_mb")
            if isinstance(start, int) and start > 0:
                bsl[mode_key] = start

        if not rec:
            skipped.append(f"{path.name}: recommended_budget_mb has no usable entries")
            continue

        profiles.append(
            ProfileData(
                path=path,
                engine_id=engine_id,
                preset_key=preset_key,
                gpu_normalized=gpu,
                recommended_budget_mb=rec,
                baselines_start_mb=bsl,
            )
        )

    return profiles, skipped


# ---------------------------------------------------------------------------
# Budget derivation
# ---------------------------------------------------------------------------


def _round_up_1000(value: int) -> int:
    if value <= 0:
        return 0
    return ((value + 999) // 1000) * 1000


@dataclass
class ConflictReport:
    """Detected disagreement between two profiles for the same cell."""

    preset_key: str
    gpu: str
    mode_key: str
    profile_a: Path
    value_a: int
    profile_b: Path
    value_b: int


@dataclass
class DerivationResult:
    """Outcome of ``derive_budgets``."""

    # {preset_key: {gpu: {mode_key: budget_mb}}}
    budgets: dict[str, dict[str, dict[str, int]]] = field(default_factory=dict)
    conflicts: list[ConflictReport] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def derive_budgets(
    profiles: list[ProfileData], headroom_mb: int = 500
) -> DerivationResult:
    """Build the ``vram_budget_by_gpu`` map from raw profile data.

    Solo budgets are taken directly from each profile's
    ``recommended_budget_mb.solo``. Coloc budgets subtract the *other*
    engine's ``baselines.solo.start_mb`` from the subject's coloc-mode
    budget, then round up.

    Conflict detection: if two profiles cover the same ``(preset_key,
    gpu, mode_key)`` triple with different budgets, both are recorded
    in ``conflicts`` and the cell is left out of the result.
    """
    result = DerivationResult()

    # Index by (preset_key, gpu) -> list of profiles. Lets us spot conflicts.
    by_cell: dict[tuple[str, str], list[ProfileData]] = {}
    for p in profiles:
        by_cell.setdefault((p.preset_key, p.gpu_normalized), []).append(p)

    # First pass: collect solo budgets + baselines per (preset_key, gpu).
    # This is what coloc derivation needs from "the other engine".
    solo_baselines: dict[tuple[str, str], int] = {}
    raw_per_cell: dict[tuple[str, str], dict[str, list[tuple[Path, int]]]] = {}

    for (preset_key, gpu), plist in by_cell.items():
        merged: dict[str, list[tuple[Path, int]]] = {}
        for p in plist:
            for mode_key, mb in p.recommended_budget_mb.items():
                merged.setdefault(mode_key, []).append((p.path, mb))
            # Pick the solo baseline (used by coloc subtraction for other engines).
            if "solo" in p.baselines_start_mb:
                solo_baselines[(preset_key, gpu)] = p.baselines_start_mb["solo"]
        raw_per_cell[(preset_key, gpu)] = merged

    # Second pass: detect conflicts + materialize budgets.
    for (preset_key, gpu), merged in raw_per_cell.items():
        for mode_key, entries in merged.items():
            unique = {v for _, v in entries}
            if len(unique) > 1:
                # Multiple profiles disagree. Report the first conflict pair
                # and skip this cell.
                (pa, va), (pb, vb) = entries[0], entries[1]
                result.conflicts.append(
                    ConflictReport(
                        preset_key=preset_key,
                        gpu=gpu,
                        mode_key=mode_key,
                        profile_a=pa,
                        value_a=va,
                        profile_b=pb,
                        value_b=vb,
                    )
                )
                continue

            raw_budget = entries[0][1]
            if mode_key == "solo":
                result.budgets.setdefault(preset_key, {}).setdefault(gpu, {})[
                    "solo"
                ] = raw_budget
                continue

            if mode_key.startswith(_COLOC_PREFIX):
                other_preset_key = mode_key[len(_COLOC_PREFIX) :]
                other_solo = solo_baselines.get((other_preset_key, gpu))
                if other_solo is None:
                    result.notes.append(
                        f"{preset_key} on {gpu}: skipping {mode_key} — no solo "
                        f"baseline available for {other_preset_key} on {gpu} "
                        f"(run solo calibration on {other_preset_key} first)"
                    )
                    continue
                # Subject's coloc budget INCLUDES other's weights;
                # subtract them out, add headroom, round up.
                clean = _round_up_1000(raw_budget - other_solo + headroom_mb)
                if clean <= 0:
                    result.notes.append(
                        f"{preset_key} on {gpu}: {mode_key} clean budget "
                        f"resolved to <= 0 (raw={raw_budget}, "
                        f"other_solo={other_solo}, headroom={headroom_mb}); "
                        f"skipping"
                    )
                    continue
                result.budgets.setdefault(preset_key, {}).setdefault(gpu, {})[
                    mode_key
                ] = clean
                continue

            result.notes.append(
                f"{preset_key} on {gpu}: unknown mode key {mode_key!r}; skipping"
            )

    return result


# ---------------------------------------------------------------------------
# Target rewriting (AST-driven)
# ---------------------------------------------------------------------------


def build_engine_id_to_preset_key(target_path: Path) -> dict[str, str]:
    """Parse ``GPU_ENGINE_PRESETS`` and return ``{engine_id: preset_key}``."""
    tree = ast.parse(target_path.read_text())
    mapping: dict[str, str] = {}
    for node in tree.body:
        if not _is_gpu_presets_node(node):
            continue
        assert isinstance(node, ast.AnnAssign)
        if not isinstance(node.value, ast.Dict):
            continue
        for key_node, value_node in zip(
            node.value.keys, node.value.values, strict=True
        ):
            if not isinstance(key_node, ast.Constant) or not isinstance(
                value_node, ast.Dict
            ):
                continue
            preset_key = key_node.value
            for sub_k, sub_v in zip(value_node.keys, value_node.values, strict=True):
                if (
                    isinstance(sub_k, ast.Constant)
                    and sub_k.value == "engine_id"
                    and isinstance(sub_v, ast.Constant)
                    and isinstance(sub_v.value, str)
                ):
                    mapping[sub_v.value] = preset_key
        break
    return mapping


def _is_gpu_presets_node(node: ast.stmt) -> bool:
    if not isinstance(node, ast.AnnAssign):
        return False
    target = node.target
    return isinstance(target, ast.Name) and target.id == "GPU_ENGINE_PRESETS"


def _line_col_to_offset(source: str, lineno: int, col_offset: int) -> int:
    """Convert (1-based line, 0-based col) into a byte offset."""
    lines = source.splitlines(keepends=True)
    return sum(len(line) for line in lines[: lineno - 1]) + col_offset


def _find_vram_budget_spans(
    source: str, target_preset_keys: set[str]
) -> dict[str, tuple[tuple[int, int], dict[str, dict[str, int]]]]:
    """Locate each preset's ``vram_budget_by_gpu`` value-dict.

    Returns ``{preset_key: ((start, end), existing_dict)}`` where
    ``(start, end)`` are byte offsets of the value-dict literal and
    ``existing_dict`` is the parsed Python equivalent. The latter lets
    the rewriter merge per-GPU cells instead of replacing the whole map.
    """
    tree = ast.parse(source)
    spans: dict[str, tuple[tuple[int, int], dict[str, dict[str, int]]]] = {}
    for node in tree.body:
        if not _is_gpu_presets_node(node):
            continue
        assert isinstance(node, ast.AnnAssign)
        if not isinstance(node.value, ast.Dict):
            return spans
        for key_node, value_node in zip(
            node.value.keys, node.value.values, strict=True
        ):
            if not isinstance(key_node, ast.Constant) or not isinstance(
                value_node, ast.Dict
            ):
                continue
            preset_key = key_node.value
            if preset_key not in target_preset_keys:
                continue
            for sub_k, sub_v in zip(value_node.keys, value_node.values, strict=True):
                if (
                    isinstance(sub_k, ast.Constant)
                    and sub_k.value == "vram_budget_by_gpu"
                    and isinstance(sub_v, ast.Dict)
                    and sub_v.end_lineno is not None
                    and sub_v.end_col_offset is not None
                ):
                    start = _line_col_to_offset(source, sub_v.lineno, sub_v.col_offset)
                    end = _line_col_to_offset(
                        source, sub_v.end_lineno, sub_v.end_col_offset
                    )
                    existing = _literal_dict_to_python(sub_v)
                    spans[preset_key] = ((start, end), existing)
        break
    return spans


def _literal_dict_to_python(node: ast.Dict) -> dict[str, dict[str, int]]:
    """Materialize a `{gpu: {mode: mb, ...}, ...}` literal as a Python dict.

    Tolerant of unexpected shapes — anything that doesn't fit the
    `{str: {str: int}}` schema is silently dropped so the rewriter
    falls back to whatever ``derived`` contributes.
    """
    out: dict[str, dict[str, int]] = {}
    for k, v in zip(node.keys, node.values, strict=True):
        if not isinstance(k, ast.Constant) or not isinstance(k.value, str):
            continue
        if not isinstance(v, ast.Dict):
            continue
        inner: dict[str, int] = {}
        for sk, sv in zip(v.keys, v.values, strict=True):
            if not isinstance(sk, ast.Constant) or not isinstance(sk.value, str):
                continue
            if isinstance(sv, ast.Constant) and isinstance(sv.value, int):
                inner[sk.value] = sv.value
        if inner:
            out[k.value] = inner
    return out


def _format_budget_dict(budgets_per_gpu: dict[str, dict[str, int]]) -> str:
    """Render the budget map as compact, readable Python source.

    Output gets fed through ruff-format by the pre-commit hook so exact
    spacing isn't load-bearing; this format just keeps PR diffs reviewable.
    """
    lines = ["{"]
    # Stable order: GPUs alphabetical, modes with solo first then coloc_with_* alpha.
    for gpu in sorted(budgets_per_gpu):
        modes = budgets_per_gpu[gpu]
        sorted_modes = sorted(
            modes.items(),
            key=lambda kv: (0 if kv[0] == "solo" else 1, kv[0]),
        )
        inner = ", ".join(f'"{m}": {v}' for m, v in sorted_modes)
        lines.append(f'            "{gpu}": {{{inner}}},')
    lines.append("        }")
    return "\n".join(lines)


def apply_rewrites(
    source: str,
    derived: dict[str, dict[str, dict[str, int]]],
) -> str:
    """Replace each affected preset's ``vram_budget_by_gpu`` value-dict.

    Only presets that already declare ``vram_budget_by_gpu`` are
    rewritten. Per-GPU cells are merged: GPUs covered by ``derived``
    are overwritten, GPUs not covered are preserved from the existing
    map. So running the sync with only T4 profile data on hand never
    silently drops the hand-seeded A10G / L4 values.
    """
    found = _find_vram_budget_spans(source, set(derived.keys()))

    # Apply replacements end-first so earlier byte offsets stay valid.
    edits: list[tuple[tuple[int, int], str]] = []
    for preset_key, ((start, end), existing) in found.items():
        if preset_key not in derived:
            continue
        merged: dict[str, dict[str, int]] = {**existing}
        for gpu, cells in derived[preset_key].items():
            merged[gpu] = cells
        edits.append(((start, end), _format_budget_dict(merged)))

    new_source = source
    for (start, end), replacement in sorted(edits, key=lambda x: x[0][0], reverse=True):
        new_source = new_source[:start] + replacement + new_source[end:]
    return new_source


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="dalston.tools.sync_vram_presets",
        description=(
            "Rewrite vram_budget_by_gpu in dalston-aws from calibrator profile JSONs."
        ),
    )
    parser.add_argument(
        "--profiles-dir",
        type=Path,
        default=DEFAULT_PROFILES_DIR,
        help=f"Profile JSON directory (default: {DEFAULT_PROFILES_DIR})",
    )
    parser.add_argument(
        "--target",
        type=Path,
        default=DEFAULT_TARGET,
        help=f"Python source to rewrite (default: {DEFAULT_TARGET})",
    )
    parser.add_argument(
        "--headroom-mb",
        type=int,
        default=500,
        help=(
            "Headroom added when subtracting the other engine's solo "
            "baseline from a coloc budget. Default: 500."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the unified diff without writing.",
    )

    args = parser.parse_args(argv)

    if not args.target.exists():
        print(f"target {args.target} not found", file=sys.stderr)
        return 1

    engine_id_to_preset = build_engine_id_to_preset_key(args.target)
    if not engine_id_to_preset:
        print(
            f"could not locate GPU_ENGINE_PRESETS in {args.target}",
            file=sys.stderr,
        )
        return 1

    profiles, skipped = load_profiles(args.profiles_dir, engine_id_to_preset)
    for msg in skipped:
        print(f"skip: {msg}", file=sys.stderr)

    if not profiles:
        print("no usable profiles found; nothing to do", file=sys.stderr)
        return 0

    result = derive_budgets(profiles, headroom_mb=args.headroom_mb)

    for note in result.notes:
        print(f"note: {note}", file=sys.stderr)

    if result.conflicts:
        for c in result.conflicts:
            print(
                f"conflict: {c.preset_key} on {c.gpu} {c.mode_key}: "
                f"{c.profile_a.name}={c.value_a} vs {c.profile_b.name}={c.value_b}",
                file=sys.stderr,
            )
        print(
            "refusing to write while conflicts are unresolved; "
            "delete the stale profile and re-run.",
            file=sys.stderr,
        )
        return 2

    if not result.budgets:
        print("no budgets derived; nothing to write.", file=sys.stderr)
        return 0

    source = args.target.read_text()
    new_source = apply_rewrites(source, result.budgets)

    if new_source == source:
        print("no changes — presets already in sync with profiles.")
        return 0

    diff = "".join(
        difflib.unified_diff(
            source.splitlines(keepends=True),
            new_source.splitlines(keepends=True),
            fromfile=str(args.target),
            tofile=str(args.target),
            n=3,
        )
    )

    if args.dry_run:
        sys.stdout.write(diff)
        return 0

    args.target.write_text(new_source)
    print(f"wrote {args.target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
