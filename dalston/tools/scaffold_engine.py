"""
Scaffold a new Dalston engine with all required files.

Usage:
    python -m dalston.tools.scaffold_engine my-engine --stage transcribe
    python -m dalston.tools.scaffold_engine my-engine --stage diarize --gpu required
    python -m dalston.tools.scaffold_engine whisper --stage transcribe --variants base,large-v3,large-v3-turbo
    python -m dalston.tools.scaffold_engine --list-stages
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import NamedTuple

# Valid pipeline stages from the JSON Schema
VALID_STAGES = [
    "prepare",
    "transcribe",
    "align",
    "diarize",
    "pii_detect",
    "audio_redact",
    "refine",
    "merge",
]

# Pipeline tag mapping for HF compatibility
PIPELINE_TAG_MAP = {
    "prepare": "dalston:audio-preparation",
    "transcribe": "automatic-speech-recognition",
    "align": "automatic-speech-recognition",
    "diarize": "speaker-diarization",
    "pii_detect": "audio-classification",
    "audio_redact": "dalston:audio-redaction",
    "refine": "automatic-speech-recognition",
    "merge": "dalston:merge",
}

# Default paths
DEFAULT_ENGINES_DIR = Path(__file__).parent.parent.parent / "engines"


class ScaffoldConfig(NamedTuple):
    """Configuration for scaffolding a new engine."""

    engine_id: str
    stage: str
    name: str
    description: str
    gpu: str  # required, optional, none
    memory: str
    languages: list[str] | None
    word_timestamps: bool
    supports_cpu: bool
    min_vram_gb: int | None
    min_ram_gb: int


class VariantConfig(NamedTuple):
    """Configuration for a single engine variant."""

    variant_id: str  # e.g., "base", "large-v3"
    engine_family: str  # e.g., "whisper"
    full_engine_id: str  # e.g., "whisper-base"
    stage: str
    name: str
    description: str
    gpu: str
    memory: str
    languages: list[str] | None
    word_timestamps: bool
    supports_cpu: bool
    min_vram_gb: int | None
    min_ram_gb: int


# Default variant hardware specs (can be overridden)
VARIANT_DEFAULTS = {
    "tiny": {"min_vram_gb": 1, "gpu": "optional", "supports_cpu": True, "memory": "2G"},
    "base": {"min_vram_gb": 2, "gpu": "optional", "supports_cpu": True, "memory": "4G"},
    "small": {
        "min_vram_gb": 3,
        "gpu": "optional",
        "supports_cpu": True,
        "memory": "4G",
    },
    "medium": {
        "min_vram_gb": 4,
        "gpu": "optional",
        "supports_cpu": False,
        "memory": "6G",
    },
    "large-v1": {
        "min_vram_gb": 6,
        "gpu": "required",
        "supports_cpu": False,
        "memory": "8G",
    },
    "large-v2": {
        "min_vram_gb": 6,
        "gpu": "required",
        "supports_cpu": False,
        "memory": "8G",
    },
    "large-v3": {
        "min_vram_gb": 6,
        "gpu": "required",
        "supports_cpu": False,
        "memory": "8G",
    },
    "large-v3-turbo": {
        "min_vram_gb": 4,
        "gpu": "required",
        "supports_cpu": False,
        "memory": "6G",
    },
    "0.6b": {
        "min_vram_gb": 4,
        "gpu": "required",
        "supports_cpu": False,
        "memory": "6G",
    },
    "1.1b": {
        "min_vram_gb": 6,
        "gpu": "required",
        "supports_cpu": False,
        "memory": "8G",
    },
}


def validate_engine_id(engine_id: str) -> str | None:
    """Validate engine ID matches schema pattern.

    Returns error message if invalid, None if valid.
    """
    pattern = r"^[a-z][a-z0-9.-]*[a-z0-9]$"
    if not re.match(pattern, engine_id):
        return (
            f"Engine ID '{engine_id}' is invalid. "
            "Must be lowercase, start with letter, end with letter/number, "
            "and contain only letters, numbers, hyphens, and dots."
        )
    if len(engine_id) < 2 or len(engine_id) > 64:
        return f"Engine ID must be 2-64 characters, got {len(engine_id)}"
    return None


def to_class_name(engine_id: str) -> str:
    """Convert engine ID to Python class name.

    Examples:
        faster-whisper -> FasterWhisperEngine
        pyannote-4.0 -> Pyannote40Engine
        my-new-engine -> MyNewEngineEngine
    """
    parts = re.split(r"[-.]", engine_id)
    class_name = "".join(part.capitalize() for part in parts)
    return f"{class_name}Engine"


def to_human_name(engine_id: str) -> str:
    """Convert engine ID to human-readable name.

    Examples:
        faster-whisper -> Faster Whisper
        pyannote-4.0 -> Pyannote 4.0
    """
    parts = engine_id.split("-")
    return " ".join(part.title() for part in parts)


def generate_engine_yaml(config: ScaffoldConfig) -> str:
    """Generate engine.yaml content."""
    pipeline_tag = PIPELINE_TAG_MAP.get(config.stage, "audio-classification")

    # Format languages
    if config.languages is None:
        languages_yaml = "    - all  # Supports all languages"
    elif config.languages == ["en"]:
        languages_yaml = "    - en"
    else:
        languages_yaml = "\n".join(f"    - {lang}" for lang in config.languages)

    # Hardware section
    hardware_lines = []
    if config.min_vram_gb:
        hardware_lines.append(f"  min_vram_gb: {config.min_vram_gb}")
    hardware_lines.append(
        "  recommended_gpu:\n    - t4\n    - a10g" if config.gpu != "none" else ""
    )
    hardware_lines.append(f"  supports_cpu: {str(config.supports_cpu).lower()}")
    hardware_lines.append(f"  min_ram_gb: {config.min_ram_gb}")
    hardware_yaml = "\n".join(line for line in hardware_lines if line)

    # Performance section based on stage defaults
    if config.stage == "transcribe":
        rtf_gpu = "0.1"
        rtf_cpu = "1.0" if config.supports_cpu else "null"
    elif config.stage == "diarize":
        rtf_gpu = "0.2"
        rtf_cpu = "null"
    elif config.stage == "align":
        rtf_gpu = "0.05"
        rtf_cpu = "0.5" if config.supports_cpu else "null"
    else:
        rtf_gpu = "0.1" if config.gpu != "none" else "null"
        rtf_cpu = "0.5" if config.supports_cpu else "null"

    return f"""schema_version: "1.1"
id: {config.engine_id}
stage: {config.stage}
name: {config.name}
version: 1.0.0
description: |
  {config.description}

container:
  gpu: {config.gpu}
  memory: {config.memory}
  model_cache: /models

capabilities:
  languages:
{languages_yaml}
  max_audio_duration: 7200
  streaming: false
  word_timestamps: {str(config.word_timestamps).lower()}

input:
  audio_formats:
    - wav
  sample_rate: 16000
  channels: 1

config_schema:
  type: object
  properties:
    # Add your engine-specific configuration options here
    model:
      type: string
      default: default
      description: Model variant to use
  additionalProperties: false

output_schema:
  type: object
  required:
    - result
  properties:
    result:
      type: object
      description: Engine output data

hf_compat:
  pipeline_tag: {pipeline_tag}
  library_name: custom
  license: apache-2.0

hardware:
{hardware_yaml}

performance:
  rtf_gpu: {rtf_gpu}
  rtf_cpu: {rtf_cpu}
  warm_start_latency_ms: 100
"""


def generate_engine_py(config: ScaffoldConfig) -> str:
    """Generate engine.py content."""
    class_name = to_class_name(config.engine_id)

    return f'''"""{config.name} engine.

{config.description}
"""

from typing import Any

from dalston.engine_sdk import (
    Engine,
    EngineCapabilities,
    TaskInput,
    TaskOutput,
)


class {class_name}(Engine):
    """{config.name} engine implementation.

    TODO: Add your model loading and processing logic here.
    """

    def __init__(self) -> None:
        super().__init__()
        self._model = None

    def _load_model(self, config: dict) -> None:
        """Load the model if not already loaded.

        Args:
            config: Engine configuration from task input
        """
        if self._model is not None:
            return

        self.logger.info("loading_model")
        # TODO: Load your model here
        # self._model = load_your_model(config.get("model", "default"))
        self._model = "placeholder"
        self.logger.info("model_loaded")

    def process(self, input: TaskInput) -> TaskOutput:
        """Process audio input.

        Args:
            input: Task input with audio file path and config

        Returns:
            TaskOutput with processing results
        """
        audio_path = input.audio_path
        config = input.config

        # Load model (lazy loading, cached)
        self._load_model(config)

        self.logger.info("processing", audio_path=str(audio_path))

        # TODO: Implement your processing logic here
        # result = self._model.process(audio_path, **config)
        result = {{"status": "processed", "audio_path": str(audio_path)}}

        self.logger.info("processing_complete")

        return TaskOutput(data=result)

    def health_check(self) -> dict[str, Any]:
        """Return health status."""
        return {{
            "status": "healthy",
            "model_loaded": self._model is not None,
        }}

    def get_capabilities(self) -> EngineCapabilities:
        """Return engine capabilities for catalog validation."""
        return EngineCapabilities(
            engine_id="{config.engine_id}",
            version="1.0.0",
            stages=["{config.stage}"],
            languages={repr(config.languages)},
            supports_word_timestamps={config.word_timestamps},
            supports_streaming=False,
            gpu_required={config.gpu == "required"},
            gpu_vram_mb={config.min_vram_gb * 1024 if config.min_vram_gb else "None"},
        )


if __name__ == "__main__":
    engine = {class_name}()
    engine.run()
'''


def generate_dockerfile(config: ScaffoldConfig) -> str:
    """Generate Dockerfile content."""
    gpu_comment = ""
    if config.gpu == "required":
        gpu_comment = "# Requires GPU - use docker compose --profile gpu\n"
    elif config.gpu == "optional":
        gpu_comment = "# GPU optional - auto-detects and falls back to CPU\n"

    return f"""# {config.name} Engine
#
# {config.description}
#
# Build from repo root:
#   docker compose build stt-batch-{config.stage}-{config.engine_id}
#
{gpu_comment}
FROM python:3.11-slim

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \\
    ffmpeg \\
    && rm -rf /var/lib/apt/lists/*

# Set working directory for dalston package
WORKDIR /opt/dalston

# Copy the dalston package source
COPY pyproject.toml .
COPY dalston/ dalston/

# Install the dalston engine SDK
RUN pip install --no-cache-dir -e ".[engine-sdk]"

# Set working directory for engine
WORKDIR /engine

# Copy engine requirements first for better caching
COPY engines/stt-{config.stage}/{config.engine_id}/requirements.txt .

# Install engine dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy engine files
COPY engines/stt-{config.stage}/{config.engine_id}/engine.yaml .
COPY engines/stt-{config.stage}/{config.engine_id}/engine.py .

# Create model cache directory
ENV HF_HOME=/models
RUN mkdir -p /models

CMD ["python", "engine.py"]
"""


def generate_requirements_txt(config: ScaffoldConfig) -> str:
    """Generate requirements.txt content."""
    base_deps = ["# Engine-specific dependencies"]

    if config.stage == "transcribe":
        base_deps.extend(["# faster-whisper>=1.0.0", "# torch>=2.0.0"])
    elif config.stage == "diarize":
        base_deps.extend(["# pyannote.audio>=3.1.0", "# torch>=2.0.0"])
    elif config.stage == "align":
        base_deps.extend(["# transformers>=4.35.0", "# torch>=2.0.0"])
    else:
        base_deps.append("# Add your dependencies here")

    return "\n".join(base_deps) + "\n"


def generate_readme(config: ScaffoldConfig) -> str:
    """Generate README.md content."""
    return f"""# {config.name}

{config.description}

## Quick Start

```bash
# Build the engine
docker compose build stt-batch-{config.stage}-{config.engine_id}

# Run with docker compose
docker compose up -d stt-batch-{config.stage}-{config.engine_id}
```

## Configuration

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `model` | string | `default` | Model variant to use |

## Hardware Requirements

- **GPU**: {config.gpu}
- **Memory**: {config.memory}
{f"- **Min VRAM**: {config.min_vram_gb}GB" if config.min_vram_gb else ""}
- **Min RAM**: {config.min_ram_gb}GB
- **CPU Support**: {"Yes" if config.supports_cpu else "No"}

## Development

```bash
# Install dependencies
pip install -r requirements.txt

# Run locally
REDIS_URL=redis://localhost:6379 ENGINE_ID={config.engine_id} python engine.py
```

## Output Format

```json
{{
  "result": {{
    "status": "processed"
  }}
}}
```
"""


def generate_variant_yaml(config: VariantConfig) -> str:
    """Generate variant-specific engine.yaml content."""
    pipeline_tag = PIPELINE_TAG_MAP.get(config.stage, "audio-classification")

    # Format languages
    if config.languages is None:
        languages_yaml = "    - all  # Supports all languages"
    elif config.languages == ["en"]:
        languages_yaml = "    - en"
    else:
        languages_yaml = "\n".join(f"    - {lang}" for lang in config.languages)

    # Hardware section
    hardware_lines = []
    if config.min_vram_gb:
        hardware_lines.append(f"  min_vram_gb: {config.min_vram_gb}")
    if config.gpu != "none":
        hardware_lines.append("  recommended_gpu:\n    - t4\n    - a10g")
    hardware_lines.append(f"  supports_cpu: {str(config.supports_cpu).lower()}")
    hardware_lines.append(f"  min_ram_gb: {config.min_ram_gb}")
    hardware_yaml = "\n".join(line for line in hardware_lines if line)

    # Performance section
    if config.stage == "transcribe":
        rtf_gpu = "0.05"
        rtf_cpu = "0.8" if config.supports_cpu else "null"
    else:
        rtf_gpu = "0.1" if config.gpu != "none" else "null"
        rtf_cpu = "0.5" if config.supports_cpu else "null"

    return f"""schema_version: "1.1"
id: {config.full_engine_id}
stage: {config.stage}
name: {config.name}
version: 1.0.0
description: |
  {config.description}

container:
  gpu: {config.gpu}
  memory: {config.memory}
  model_cache: /models

capabilities:
  languages:
{languages_yaml}
  max_audio_duration: 7200
  streaming: false
  word_timestamps: {str(config.word_timestamps).lower()}

input:
  audio_formats:
    - wav
  sample_rate: 16000
  channels: 1

hf_compat:
  pipeline_tag: {pipeline_tag}
  library_name: custom
  license: apache-2.0

hardware:
{hardware_yaml}

performance:
  rtf_gpu: {rtf_gpu}
  rtf_cpu: {rtf_cpu}
  warm_start_latency_ms: 100
"""


def generate_variant_dockerfile(engine_family: str, stage: str) -> str:
    """Generate parameterized Dockerfile for variant-based engines."""
    return f"""# {engine_family.title()} Engine (Parameterized)
#
# Build with specific variant:
#   docker compose build --build-arg MODEL_VARIANT=base stt-batch-{stage}-{engine_family}-base
#   docker compose build --build-arg MODEL_VARIANT=large-v3 stt-batch-{stage}-{engine_family}-large-v3
#
ARG MODEL_VARIANT=large-v3

FROM python:3.11-slim

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \\
    ffmpeg \\
    && rm -rf /var/lib/apt/lists/*

# Set working directory for dalston package
WORKDIR /opt/dalston

# Copy the dalston package source
COPY pyproject.toml .
COPY dalston/ dalston/

# Install the dalston engine SDK
RUN pip install --no-cache-dir -e ".[engine-sdk]"

# Set working directory for engine
WORKDIR /engine

# Copy engine requirements first for better caching
COPY engines/stt-{stage}/{engine_family}/requirements.txt .

# Install engine dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy engine implementation
COPY engines/stt-{stage}/{engine_family}/engine.py .

# Copy variant-specific config to standard location
COPY engines/stt-{stage}/{engine_family}/variants/${{MODEL_VARIANT}}.yaml /etc/dalston/engine.yaml

# Set model variant via environment
ENV MODEL_VARIANT=${{MODEL_VARIANT}}
ENV HF_HOME=/models
RUN mkdir -p /models

CMD ["python", "engine.py"]
"""


def generate_variant_engine_py(engine_family: str, stage: str, description: str) -> str:
    """Generate shared engine.py for variant-based engines."""
    class_name = to_class_name(engine_family)

    return f'''"""{engine_family.title()} engine.

{description}

This engine supports multiple model variants configured via the MODEL_VARIANT
environment variable. Each variant has its own engine.yaml in the variants/
directory with appropriate hardware requirements.
"""

import os
from typing import Any

from dalston.engine_sdk import (
    Engine,
    TaskInput,
    TaskOutput,
)


class {class_name}(Engine):
    """{engine_family.title()} engine implementation.

    The model variant is determined by the MODEL_VARIANT environment variable,
    which is set at container build time. Each variant is a separate
    deployable engine with its own hardware requirements.

    TODO: Add your model loading and processing logic here.
    """

    def __init__(self) -> None:
        super().__init__()
        self._model = None
        # Model variant determined by container, not request
        self._model_variant = os.environ.get("MODEL_VARIANT", "large-v3")
        self.logger.info("engine_init", model_variant=self._model_variant)

    def _load_model(self) -> None:
        """Load the model if not already loaded."""
        if self._model is not None:
            return

        self.logger.info("loading_model", model_variant=self._model_variant)
        # TODO: Load your model here based on self._model_variant
        # self._model = load_model(self._model_variant)
        self._model = "placeholder"
        self.logger.info("model_loaded", model_variant=self._model_variant)

    def process(self, input: TaskInput) -> TaskOutput:
        """Process audio input.

        Args:
            input: Task input with audio file path and config

        Returns:
            TaskOutput with processing results
        """
        audio_path = input.audio_path

        # Load model (lazy loading, cached)
        self._load_model()

        self.logger.info("processing", audio_path=str(audio_path))

        # TODO: Implement your processing logic here
        # result = self._model.process(audio_path)
        result = {{"status": "processed", "audio_path": str(audio_path)}}

        self.logger.info("processing_complete")

        return TaskOutput(data=result)

    def health_check(self) -> dict[str, Any]:
        """Return health status."""
        return {{
            "status": "healthy",
            "model_loaded": self._model is not None,
            "model_size": self._model_variant,
        }}


if __name__ == "__main__":
    engine = {class_name}()
    engine.run()
'''


def scaffold_variant_engine(
    engine_family: str,
    stage: str,
    variants: list[str],
    description: str,
    languages: list[str] | None,
    word_timestamps: bool,
    engines_dir: Path,
    dry_run: bool,
) -> bool:
    """Create engine directory with variant structure.

    Returns True on success, False on failure.
    """
    engine_dir = engines_dir / f"stt-{stage}" / engine_family

    if engine_dir.exists():
        print(f"Error: Directory already exists: {engine_dir}", file=sys.stderr)
        return False

    # Generate files
    files = {
        "engine.py": generate_variant_engine_py(engine_family, stage, description),
        "Dockerfile": generate_variant_dockerfile(engine_family, stage),
        "requirements.txt": "# Engine-specific dependencies\n# Add your dependencies here\n",
        "README.md": f"""# {engine_family.title()}

{description}

## Variants

This engine supports multiple variants with different hardware requirements:

| Variant | Command |
|---------|---------|
{chr(10).join(f"| {v} | `docker compose up stt-batch-{stage}-{engine_family}-{v}` |" for v in variants)}

## Adding a New Variant

1. Create `variants/{{new-variant}}.yaml` with appropriate hardware specs
2. Add service to `docker-compose.yml`
3. Build: `docker compose build --build-arg MODEL_VARIANT={{new-variant}} stt-batch-{stage}-{engine_family}-{{new-variant}}`
""",
    }

    # Generate variant YAML files
    variant_files = {}
    for variant in variants:
        defaults = VARIANT_DEFAULTS.get(variant, {})
        config = VariantConfig(
            variant_id=variant,
            engine_family=engine_family,
            full_engine_id=f"{engine_family}-{variant}",
            stage=stage,
            name=f"{engine_family.title()} {variant.title().replace('-', ' ')}",
            description=f"{engine_family.title()} {variant} variant.",
            gpu=defaults.get("gpu", "optional"),
            memory=defaults.get("memory", "4G"),
            languages=languages,
            word_timestamps=word_timestamps,
            supports_cpu=defaults.get("supports_cpu", True),
            min_vram_gb=defaults.get("min_vram_gb", 4),
            min_ram_gb=4,
        )
        variant_files[f"variants/{variant}.yaml"] = generate_variant_yaml(config)

    if dry_run:
        print(f"Would create: {engine_dir}/")
        for filename in files:
            print(f"  - {filename}")
        print("  - variants/")
        for filename in variant_files:
            print(f"    - {filename.split('/')[1]}")
        print("\nUse --no-dry-run to create files.")
        return True

    # Create directories
    engine_dir.mkdir(parents=True, exist_ok=True)
    (engine_dir / "variants").mkdir(exist_ok=True)

    # Write main files
    for filename, content in files.items():
        file_path = engine_dir / filename
        file_path.write_text(content)
        print(f"Created: {file_path}")

    # Write variant files
    for filename, content in variant_files.items():
        file_path = engine_dir / filename
        file_path.write_text(content)
        print(f"Created: {file_path}")

    print(f"\n✓ Engine with {len(variants)} variants scaffolded at {engine_dir}")
    print("\nNext steps:")
    print("  1. Implement process() in engine.py")
    print("  2. Add dependencies to requirements.txt")
    print("  3. Adjust hardware specs in variants/*.yaml")
    print(
        f"  4. Validate: python -m dalston.tools.validate_engine {engine_dir}/variants/*.yaml"
    )
    print("  5. Add services to docker-compose.yml")

    return True


def scaffold_engine(config: ScaffoldConfig, engines_dir: Path, dry_run: bool) -> bool:
    """Create the engine directory and all template files.

    Returns True on success, False on failure.
    """
    engine_dir = engines_dir / f"stt-{config.stage}" / config.engine_id

    if engine_dir.exists():
        print(f"Error: Directory already exists: {engine_dir}", file=sys.stderr)
        return False

    files = {
        "engine.yaml": generate_engine_yaml(config),
        "engine.py": generate_engine_py(config),
        "Dockerfile": generate_dockerfile(config),
        "requirements.txt": generate_requirements_txt(config),
        "README.md": generate_readme(config),
    }

    if dry_run:
        print(f"Would create: {engine_dir}/")
        for filename in files:
            print(f"  - {filename}")
        print("\nUse --no-dry-run to create files.")
        return True

    # Create directory
    engine_dir.mkdir(parents=True, exist_ok=True)

    # Write files
    for filename, content in files.items():
        file_path = engine_dir / filename
        file_path.write_text(content)
        print(f"Created: {file_path}")

    print(f"\n✓ Engine scaffolded at {engine_dir}")
    print("\nNext steps:")
    print("  1. Edit engine.yaml with your specific configuration")
    print("  2. Implement process() in engine.py")
    print("  3. Add dependencies to requirements.txt")
    print(
        f"  4. Validate: python -m dalston.tools.validate_engine {engine_dir}/engine.yaml"
    )
    print("  5. Add to docker-compose.yml")

    return True


def main() -> int:
    """Main entry point for the scaffold CLI."""
    parser = argparse.ArgumentParser(
        description="Scaffold a new Dalston engine with all required files",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m dalston.tools.scaffold_engine my-transcriber --stage transcribe
  python -m dalston.tools.scaffold_engine my-diarizer --stage diarize --gpu required
  python -m dalston.tools.scaffold_engine my-merger --stage merge --gpu none
  python -m dalston.tools.scaffold_engine whisper --stage transcribe --variants base,large-v3,large-v3-turbo
  python -m dalston.tools.scaffold_engine --list-stages
        """,
    )
    parser.add_argument(
        "engine_id",
        type=str,
        nargs="?",
        help="Engine identifier (lowercase, hyphens allowed)",
    )
    parser.add_argument(
        "--stage",
        type=str,
        choices=VALID_STAGES,
        help="Pipeline stage for this engine",
    )
    parser.add_argument(
        "--list-stages",
        action="store_true",
        help="List all valid pipeline stages",
    )
    parser.add_argument(
        "--name",
        type=str,
        help="Human-readable name (default: derived from engine_id)",
    )
    parser.add_argument(
        "--description",
        type=str,
        default="A custom Dalston engine.",
        help="Engine description",
    )
    parser.add_argument(
        "--gpu",
        type=str,
        choices=["required", "optional", "none"],
        default="optional",
        help="GPU requirement level (default: optional)",
    )
    parser.add_argument(
        "--memory",
        type=str,
        default="4G",
        help="Memory requirement (default: 4G)",
    )
    parser.add_argument(
        "--languages",
        type=str,
        nargs="+",
        default=None,
        help="Supported languages (default: all). Use 'all' for universal support.",
    )
    parser.add_argument(
        "--word-timestamps",
        action="store_true",
        help="Engine produces word-level timestamps",
    )
    parser.add_argument(
        "--variants",
        type=str,
        help="Comma-separated list of variants (e.g., base,large-v3,large-v3-turbo). "
        "Creates variant structure with separate YAML per variant.",
    )
    parser.add_argument(
        "--engines-dir",
        type=Path,
        default=DEFAULT_ENGINES_DIR,
        help=f"Engines directory (default: {DEFAULT_ENGINES_DIR})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="Show what would be created without creating files (default: true)",
    )
    parser.add_argument(
        "--no-dry-run",
        action="store_true",
        help="Actually create the files",
    )

    args = parser.parse_args()

    if args.list_stages:
        print("Valid pipeline stages:")
        for stage in VALID_STAGES:
            tag = PIPELINE_TAG_MAP.get(stage, "")
            print(f"  {stage:12} -> {tag}")
        return 0

    if not args.engine_id:
        parser.error("engine_id is required (or use --list-stages)")

    if not args.stage:
        parser.error("--stage is required")

    # Validate engine ID
    error = validate_engine_id(args.engine_id)
    if error:
        print(f"Error: {error}", file=sys.stderr)
        return 1

    # Process languages
    languages = args.languages
    if languages == ["all"]:
        languages = None

    dry_run = not args.no_dry_run

    # Handle variant-based scaffolding
    if args.variants:
        variants = [v.strip() for v in args.variants.split(",")]
        success = scaffold_variant_engine(
            engine_family=args.engine_id,
            stage=args.stage,
            variants=variants,
            description=args.description,
            languages=languages,
            word_timestamps=args.word_timestamps,
            engines_dir=args.engines_dir,
            dry_run=dry_run,
        )
        return 0 if success else 1

    # Traditional single-engine scaffolding
    # Determine hardware defaults based on GPU setting
    if args.gpu == "none":
        supports_cpu = True
        min_vram_gb = None
    elif args.gpu == "required":
        supports_cpu = False
        min_vram_gb = 4
    else:  # optional
        supports_cpu = True
        min_vram_gb = 4

    config = ScaffoldConfig(
        engine_id=args.engine_id,
        stage=args.stage,
        name=args.name or to_human_name(args.engine_id),
        description=args.description,
        gpu=args.gpu,
        memory=args.memory,
        languages=languages,
        word_timestamps=args.word_timestamps,
        supports_cpu=supports_cpu,
        min_vram_gb=min_vram_gb,
        min_ram_gb=4,
    )

    success = scaffold_engine(config, args.engines_dir, dry_run)

    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
