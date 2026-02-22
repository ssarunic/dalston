"""
Validate engine.yaml files against the Dalston engine schema.

Usage:
    python -m dalston.tools.validate_engine engines/transcribe/faster-whisper/engine.yaml
    python -m dalston.tools.validate_engine --all
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import NamedTuple

import yaml
from jsonschema import Draft7Validator

# Default paths
DEFAULT_SCHEMA_PATH = Path(__file__).parent.parent / "schemas" / "engine.schema.json"
DEFAULT_ENGINES_DIR = Path(__file__).parent.parent.parent / "engines"


class ValidationResult(NamedTuple):
    """Result of validating an engine.yaml file."""

    path: Path
    valid: bool
    engine_id: str | None
    version: str | None
    schema_version: str | None
    stage_or_type: str | None
    languages: list[str] | None
    errors: list[str]


def load_schema(schema_path: Path) -> dict:
    """Load the JSON Schema from disk."""
    with open(schema_path) as f:
        return json.load(f)


def load_engine_yaml(path: Path) -> dict:
    """Load an engine.yaml file."""
    with open(path) as f:
        return yaml.safe_load(f)


def validate_engine(
    engine_path: Path,
    schema: dict,
) -> ValidationResult:
    """Validate a single engine.yaml file against the schema."""
    errors: list[str] = []

    try:
        data = load_engine_yaml(engine_path)
    except yaml.YAMLError as e:
        return ValidationResult(
            path=engine_path,
            valid=False,
            engine_id=None,
            version=None,
            schema_version=None,
            stage_or_type=None,
            languages=None,
            errors=[f"YAML parse error: {e}"],
        )
    except FileNotFoundError:
        return ValidationResult(
            path=engine_path,
            valid=False,
            engine_id=None,
            version=None,
            schema_version=None,
            stage_or_type=None,
            languages=None,
            errors=[f"File not found: {engine_path}"],
        )

    validator = Draft7Validator(schema)
    validation_errors = list(validator.iter_errors(data))

    for error in validation_errors:
        path_str = (
            ".".join(str(p) for p in error.absolute_path)
            if error.absolute_path
            else "(root)"
        )
        errors.append(f"{path_str}: {error.message}")

    engine_id = data.get("id")
    version = data.get("version")
    schema_version = data.get("schema_version")
    stage_or_type = data.get("stage") or data.get("type")
    languages = data.get("capabilities", {}).get("languages")

    return ValidationResult(
        path=engine_path,
        valid=len(errors) == 0,
        engine_id=engine_id,
        version=version,
        schema_version=schema_version,
        stage_or_type=stage_or_type,
        languages=languages,
        errors=errors,
    )


def find_all_engine_yamls(engines_dir: Path) -> list[Path]:
    """Find all engine metadata YAML files in the engines directory.

    Includes:
    - engines/**/engine.yaml (legacy single-engine layout)
    - engines/**/variants/*.yaml (variant-based layout)

    If an engine directory has variants/*.yaml, its top-level engine.yaml is skipped
    to avoid validating stale duplicate metadata.
    """
    yamls: list[Path] = []

    for yaml_path in engines_dir.glob("**/engine.yaml"):
        variants_dir = yaml_path.parent / "variants"
        if variants_dir.exists() and any(variants_dir.glob("*.yaml")):
            continue
        yamls.append(yaml_path)

    yamls.extend(engines_dir.glob("**/variants/*.yaml"))

    return sorted(yamls)


def format_languages(languages: list[str] | None) -> str:
    """Format language list for display."""
    if languages is None:
        return "none"
    if languages == ["all"]:
        return "all"
    if len(languages) <= 5:
        return ", ".join(languages)
    return f"{', '.join(languages[:4])}, +{len(languages) - 4} more"


def print_result(result: ValidationResult, verbose: bool = False) -> None:
    """Print validation result in a human-readable format."""
    status = "\u2713" if result.valid else "\u2717"

    if result.valid:
        print(
            f"{status} {result.engine_id} v{result.version} (schema {result.schema_version})"
        )
        print(f"  Stage: {result.stage_or_type}")
        print(f"  Languages: {format_languages(result.languages)}")
    else:
        engine_info = result.engine_id or result.path.name
        print(f"{status} {engine_info}")
        print(f"  Path: {result.path}")
        for error in result.errors:
            print(f"  Error: {error}")


def main() -> int:
    """Main entry point for the validator CLI."""
    parser = argparse.ArgumentParser(
        description="Validate engine.yaml files against the Dalston schema",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m dalston.tools.validate_engine engines/transcribe/faster-whisper/engine.yaml
  python -m dalston.tools.validate_engine --all
  python -m dalston.tools.validate_engine --all --engines-dir /path/to/engines
        """,
    )
    parser.add_argument(
        "path",
        type=Path,
        nargs="?",
        help="Path to engine.yaml file to validate",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Validate all engine.yaml files in the engines directory",
    )
    parser.add_argument(
        "--engines-dir",
        type=Path,
        default=DEFAULT_ENGINES_DIR,
        help=f"Directory containing engines (default: {DEFAULT_ENGINES_DIR})",
    )
    parser.add_argument(
        "--schema",
        type=Path,
        default=DEFAULT_SCHEMA_PATH,
        help=f"Path to JSON Schema (default: {DEFAULT_SCHEMA_PATH})",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Verbose output",
    )
    parser.add_argument(
        "--quiet",
        "-q",
        action="store_true",
        help="Only output errors",
    )

    args = parser.parse_args()

    if not args.path and not args.all:
        parser.error("Either provide a path or use --all")

    if args.path and args.all:
        parser.error("Cannot use both path and --all")

    try:
        schema = load_schema(args.schema)
    except FileNotFoundError:
        print(f"Error: Schema file not found: {args.schema}", file=sys.stderr)
        return 1
    except json.JSONDecodeError as e:
        print(f"Error: Invalid JSON in schema: {e}", file=sys.stderr)
        return 1

    if args.all:
        engine_files = find_all_engine_yamls(args.engines_dir)
        if not engine_files:
            print(
                f"No engine metadata YAML files found in {args.engines_dir}",
                file=sys.stderr,
            )
            return 1
    else:
        engine_files = [args.path]

    results = [validate_engine(path, schema) for path in engine_files]
    valid_count = sum(1 for r in results if r.valid)
    total_count = len(results)

    if not args.quiet:
        for result in results:
            if not args.quiet or not result.valid:
                print_result(result, args.verbose)
                print()

    if args.all:
        summary_icon = "\u2713" if valid_count == total_count else "\u2717"
        print(f"{summary_icon} {valid_count}/{total_count} engines valid")

    return 0 if valid_count == total_count else 1


if __name__ == "__main__":
    sys.exit(main())
