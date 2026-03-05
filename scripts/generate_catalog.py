#!/usr/bin/env python3
"""Generate engine catalog from engine.yaml files.

The catalog enables:
- Runtime discovery: What engine runtimes can handle each pipeline stage
- Capability validation: Check job requirements before engines are running

M46: Model metadata has moved to the database (ModelRegistryModel). Models are
seeded from YAMLs at gateway startup, not via this catalog. This script now
only generates the engines section.

Sources:
    engines/**/engine.yaml  - Runtime metadata (all engines have runtime: field)

Usage:
    python scripts/generate_catalog.py
    python scripts/generate_catalog.py --engines-dir engines/ --output dalston/orchestrator/generated_catalog.json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import yaml


def find_runtime_yamls(engines_dir: Path) -> list[Path]:
    """Find all engine.yaml files (runtime definitions)."""
    yamls: list[Path] = []

    for yaml_path in engines_dir.glob("**/engine.yaml"):
        # Skip realtime engines for now (separate migration)
        if "stt-rt" in str(yaml_path):
            continue
        yamls.append(yaml_path)

    return sorted(yamls)


def load_yaml(path: Path) -> dict:
    """Load and parse a YAML file."""
    with open(path) as f:
        return yaml.safe_load(f)


def derive_image_name(runtime_id: str, stage: str, version: str) -> str:
    """Derive Docker image name from runtime metadata."""
    return f"dalston/stt-batch-{stage}-{runtime_id}:{version}"


def transform_runtime_to_entry(data: dict, yaml_path: Path) -> dict:
    """Transform engine.yaml data into runtime catalog entry format."""
    runtime_id = data.get("runtime", data.get("id"))
    engine_file_id = data.get("id")
    version = data.get("version", "1.0.0")
    stage = data.get("stage")

    # Derive stages list
    stages = [stage] if stage else []

    # Extract capabilities
    caps = data.get("capabilities", {})
    languages = caps.get("languages")
    if languages == ["all"]:
        languages = None  # null means all languages in catalog format

    # Extract hardware info
    hardware = data.get("hardware", {})
    container = data.get("container", {})

    # Determine GPU requirement from container.gpu field
    gpu_field = container.get("gpu", "none")
    gpu_required = gpu_field == "required"

    # Extract performance info
    performance = data.get("performance", {})

    entry = {
        "id": runtime_id,
        "runtime": engine_file_id,  # Engine file ID for capability routing
        "name": data.get("name", runtime_id),
        "version": version,
        "stage": stage,
        "description": data.get("description", "").strip(),
        "image": derive_image_name(runtime_id, stage, version),
        "capabilities": {
            "stages": stages,
            "languages": languages,
            "supports_word_timestamps": caps.get("word_timestamps", False),
            "supports_streaming": caps.get("streaming", False),
            "max_audio_duration": caps.get("max_audio_duration"),
            "max_concurrency": caps.get("max_concurrency"),
        },
        "hardware": {
            "gpu_required": gpu_required,
            "gpu_optional": gpu_field == "optional",
            "min_vram_gb": hardware.get("min_vram_gb"),
            "recommended_gpu": hardware.get("recommended_gpu"),
            "supports_cpu": hardware.get("supports_cpu", True),
            "min_ram_gb": hardware.get("min_ram_gb"),
            "memory": container.get("memory"),
        },
        "performance": {
            "rtf_gpu": performance.get("rtf_gpu"),
            "rtf_cpu": performance.get("rtf_cpu"),
            "warm_start_latency_ms": performance.get("warm_start_latency_ms"),
        },
    }

    return entry


def generate_catalog(engines_dir: Path) -> dict:
    """Generate catalog from engine.yaml files.

    M46: Only generates engine entries. Model metadata has moved to the database.
    """
    # Load runtime definitions from engine.yaml files
    runtime_yamls = find_runtime_yamls(engines_dir)
    if not runtime_yamls:
        raise ValueError(f"No engine.yaml files found in {engines_dir}")

    engines = {}
    for yaml_path in runtime_yamls:
        try:
            data = load_yaml(yaml_path)
            entry = transform_runtime_to_entry(data, yaml_path)
            runtime_id = entry["id"]
            # Build engine entry
            engines[runtime_id] = {
                "id": runtime_id,
                "name": entry["name"],
                "version": entry["version"],
                "stage": entry["stage"],
                "type": None,
                "description": entry["description"],
                "image": entry["image"],
                "capabilities": entry["capabilities"],
                "hardware": entry["hardware"],
                "performance": entry["performance"],
            }
        except Exception as e:
            print(f"Warning: Failed to process {yaml_path}: {e}", file=sys.stderr)
            continue

    catalog = {
        "generated_at": datetime.now(UTC).isoformat(),
        "schema_version": "3.0",  # M46: models removed
        "engine_count": len(engines),
        "engines": engines,
    }

    return catalog


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Generate engine catalog from engine.yaml files"
    )
    parser.add_argument(
        "--engines-dir",
        type=Path,
        default=Path(__file__).parent.parent / "engines",
        help="Directory containing engine subdirectories",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).parent.parent
        / "dalston"
        / "orchestrator"
        / "generated_catalog.json",
        help="Output path for generated catalog",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print catalog to stdout instead of writing to file",
    )

    args = parser.parse_args()

    try:
        catalog = generate_catalog(args.engines_dir)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    output_json = json.dumps(catalog, indent=2, sort_keys=False)

    if args.dry_run:
        print(output_json)
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with open(args.output, "w") as f:
            f.write(output_json)
            f.write("\n")
        print(f"Generated catalog: {catalog['engine_count']} engines: {args.output}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
