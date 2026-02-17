#!/usr/bin/env python3
"""Generate engine catalog from engine.yaml files.

This script scans all engine.yaml files in the engines directory and
generates a single catalog.json file for the orchestrator to load.

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


def find_engine_yamls(engines_dir: Path) -> list[Path]:
    """Find all engine.yaml files in the engines directory."""
    return sorted(engines_dir.glob("**/engine.yaml"))


def load_engine_yaml(path: Path) -> dict:
    """Load and parse an engine.yaml file."""
    with open(path) as f:
        return yaml.safe_load(f)


def derive_image_name(engine_id: str, stage_or_type: str, version: str) -> str:
    """Derive Docker image name from engine metadata."""
    if stage_or_type == "realtime":
        return f"dalston/stt-rt-{engine_id}:{version}"
    return f"dalston/stt-batch-{stage_or_type}-{engine_id}:{version}"


def transform_engine_to_catalog_entry(data: dict, yaml_path: Path) -> dict:
    """Transform engine.yaml data into catalog entry format."""
    engine_id = data["id"]
    version = data.get("version", "1.0.0")
    stage = data.get("stage")
    engine_type = data.get("type")
    stage_or_type = stage or engine_type

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
        "id": engine_id,
        "name": data.get("name", engine_id),
        "version": version,
        "stage": stage,
        "type": engine_type,
        "description": data.get("description", "").strip(),
        "image": derive_image_name(engine_id, stage_or_type, version),
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

    # Add hf_compat if present
    if "hf_compat" in data:
        entry["hf_compat"] = data["hf_compat"]

    # Add input formats if present
    input_spec = data.get("input", {})
    if input_spec.get("audio_formats"):
        entry["input"] = {
            "audio_formats": input_spec.get("audio_formats"),
            "sample_rate": input_spec.get("sample_rate"),
            "channels": input_spec.get("channels"),
        }

    return entry


def generate_catalog(engines_dir: Path) -> dict:
    """Generate catalog from all engine.yaml files."""
    engine_yamls = find_engine_yamls(engines_dir)

    if not engine_yamls:
        raise ValueError(f"No engine.yaml files found in {engines_dir}")

    engines = {}
    for yaml_path in engine_yamls:
        try:
            data = load_engine_yaml(yaml_path)
            entry = transform_engine_to_catalog_entry(data, yaml_path)
            engines[entry["id"]] = entry
        except Exception as e:
            print(f"Warning: Failed to process {yaml_path}: {e}", file=sys.stderr)
            continue

    catalog = {
        "generated_at": datetime.now(UTC).isoformat(),
        "schema_version": "1.1",
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
        print(
            f"Generated catalog with {catalog['engine_count']} engines: {args.output}"
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
