#!/usr/bin/env python3
"""Generate engine catalog from engine.yaml and model files.

M36: This script now generates a two-catalog output:
- runtimes: Engine runtimes that can load models (from engines/*/engine.yaml)
- models: Model variants with runtime mappings (from models/*.yaml)

The catalog enables:
- Runtime discovery: What engine runtimes can handle each pipeline stage
- Model routing: Which runtime loads each model variant
- Backward compatibility: Combined 'engines' section for existing code

Sources:
    engines/**/engine.yaml  - Runtime metadata (all engines have runtime: field)
    models/*.yaml           - Model variant metadata with runtime_model_id

Usage:
    python scripts/generate_catalog.py
    python scripts/generate_catalog.py --engines-dir engines/ --models-dir models/ --output dalston/orchestrator/generated_catalog.json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import yaml


def find_runtime_yamls(engines_dir: Path) -> list[Path]:
    """Find all engine.yaml files (runtime definitions).

    M36: All engines now have a runtime: field in their engine.yaml.
    We read the runtime-level YAML, not the variants.
    """
    yamls: list[Path] = []

    for yaml_path in engines_dir.glob("**/engine.yaml"):
        # Skip realtime engines for now (separate migration)
        if "stt-rt" in str(yaml_path):
            continue
        yamls.append(yaml_path)

    return sorted(yamls)


def find_model_yamls(models_dir: Path) -> list[Path]:
    """Find all model YAML files in the models directory."""
    if not models_dir.exists():
        return []
    return sorted(models_dir.glob("*.yaml"))


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
    engine_id = data.get("id")
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
        "engine_id": engine_id,  # Original engine ID
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


def transform_model_to_entry(data: dict) -> dict:
    """Transform model YAML data into model catalog entry format."""
    model_id = data["id"]

    # Normalize languages
    languages = data.get("languages")
    if languages == ["all"]:
        languages = None

    # Extract capabilities
    caps = data.get("capabilities", {})
    hardware = data.get("hardware", {})
    performance = data.get("performance", {})

    entry = {
        "id": model_id,
        "runtime": data["runtime"],
        "runtime_model_id": data["runtime_model_id"],
        "name": data.get("name", model_id),
        "source": data.get("source"),
        "size_gb": data.get("size_gb"),
        "stage": data.get("stage"),
        "description": data.get("description", "").strip(),
        "languages": languages,
        "capabilities": {
            "word_timestamps": caps.get("word_timestamps", False),
            "punctuation": caps.get("punctuation", False),
            "capitalization": caps.get("capitalization", False),
            "streaming": caps.get("streaming", False),
            "max_audio_duration": caps.get("max_audio_duration"),
        },
        "hardware": {
            "min_vram_gb": hardware.get("min_vram_gb"),
            "supports_cpu": hardware.get("supports_cpu", False),
            "min_ram_gb": hardware.get("min_ram_gb"),
        },
        "performance": {
            "rtf_gpu": performance.get("rtf_gpu"),
            "rtf_cpu": performance.get("rtf_cpu"),
        },
    }

    return entry


def generate_catalog(engines_dir: Path, models_dir: Path) -> dict:
    """Generate catalog from engine.yaml and model YAML files.

    M36: Generates a two-catalog structure:
    - runtimes: Engine runtime definitions
    - models: Model variant definitions with runtime mappings
    - engines: Combined for backward compatibility
    """
    # Load runtime definitions from engine.yaml files
    runtime_yamls = find_runtime_yamls(engines_dir)
    if not runtime_yamls:
        raise ValueError(f"No engine.yaml files found in {engines_dir}")

    runtimes = {}
    for yaml_path in runtime_yamls:
        try:
            data = load_yaml(yaml_path)
            entry = transform_runtime_to_entry(data, yaml_path)
            runtimes[entry["id"]] = entry
        except Exception as e:
            print(f"Warning: Failed to process {yaml_path}: {e}", file=sys.stderr)
            continue

    # Load model definitions from models/*.yaml
    model_yamls = find_model_yamls(models_dir)
    models = {}
    for yaml_path in model_yamls:
        try:
            data = load_yaml(yaml_path)
            entry = transform_model_to_entry(data)
            models[entry["id"]] = entry
        except Exception as e:
            print(f"Warning: Failed to process {yaml_path}: {e}", file=sys.stderr)
            continue

    # Build backward-compatible engines section
    # Utility engines (non-transcription) go directly
    # Transcription engines are represented by their runtime
    engines = {}
    for runtime_id, runtime in runtimes.items():
        # Copy runtime entry as engine entry for compatibility
        engines[runtime_id] = {
            "id": runtime_id,
            "name": runtime["name"],
            "version": runtime["version"],
            "stage": runtime["stage"],
            "type": None,
            "description": runtime["description"],
            "image": runtime["image"],
            "capabilities": runtime["capabilities"],
            "hardware": runtime["hardware"],
            "performance": runtime["performance"],
        }

    catalog = {
        "generated_at": datetime.now(UTC).isoformat(),
        "schema_version": "2.0",
        "runtime_count": len(runtimes),
        "model_count": len(models),
        "engine_count": len(engines),
        "runtimes": runtimes,
        "models": models,
        "engines": engines,  # Backward compatibility
    }

    return catalog


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Generate engine catalog from engine.yaml and model files"
    )
    parser.add_argument(
        "--engines-dir",
        type=Path,
        default=Path(__file__).parent.parent / "engines",
        help="Directory containing engine subdirectories",
    )
    parser.add_argument(
        "--models-dir",
        type=Path,
        default=Path(__file__).parent.parent / "models",
        help="Directory containing model YAML files",
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
        catalog = generate_catalog(args.engines_dir, args.models_dir)
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
            f"Generated catalog: {catalog['runtime_count']} runtimes, "
            f"{catalog['model_count']} models: {args.output}"
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
