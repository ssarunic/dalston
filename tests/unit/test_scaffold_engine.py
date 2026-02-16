"""Tests for the engine scaffold tool."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
import yaml

from dalston.tools.scaffold_engine import (
    PIPELINE_TAG_MAP,
    VALID_STAGES,
    ScaffoldConfig,
    generate_dockerfile,
    generate_engine_py,
    generate_engine_yaml,
    generate_readme,
    generate_requirements_txt,
    scaffold_engine,
    to_class_name,
    to_human_name,
    validate_engine_id,
)


class TestValidateEngineId:
    """Tests for validate_engine_id function."""

    def test_valid_simple_id(self) -> None:
        """Simple lowercase ID should pass."""
        assert validate_engine_id("my-engine") is None

    def test_valid_id_with_dots(self) -> None:
        """ID with dots should pass (e.g., pyannote-3.1)."""
        assert validate_engine_id("pyannote-3.1") is None

    def test_valid_id_with_numbers(self) -> None:
        """ID with numbers should pass."""
        assert validate_engine_id("whisper2") is None

    def test_invalid_uppercase(self) -> None:
        """Uppercase letters should fail."""
        result = validate_engine_id("MyEngine")
        assert result is not None
        assert "invalid" in result.lower()

    def test_invalid_starts_with_number(self) -> None:
        """ID starting with number should fail."""
        result = validate_engine_id("123-engine")
        assert result is not None

    def test_invalid_ends_with_hyphen(self) -> None:
        """ID ending with hyphen should fail."""
        result = validate_engine_id("my-engine-")
        assert result is not None

    def test_invalid_too_short(self) -> None:
        """Single character ID should fail."""
        result = validate_engine_id("a")
        assert result is not None
        assert "invalid" in result.lower()

    def test_invalid_special_characters(self) -> None:
        """ID with special characters should fail."""
        result = validate_engine_id("my_engine")
        assert result is not None


class TestToClassName:
    """Tests for to_class_name function."""

    def test_simple_hyphenated(self) -> None:
        """Hyphenated name should convert to PascalCase."""
        assert to_class_name("faster-whisper") == "FasterWhisperEngine"

    def test_with_dots(self) -> None:
        """Name with dots should convert properly."""
        assert to_class_name("pyannote-3.1") == "Pyannote31Engine"

    def test_single_word(self) -> None:
        """Single word should capitalize and add Engine suffix."""
        assert to_class_name("parakeet") == "ParakeetEngine"

    def test_multiple_hyphens(self) -> None:
        """Multiple hyphens should all be removed."""
        assert to_class_name("my-new-cool-engine") == "MyNewCoolEngineEngine"


class TestToHumanName:
    """Tests for to_human_name function."""

    def test_simple_hyphenated(self) -> None:
        """Hyphenated name should convert to title case."""
        assert to_human_name("faster-whisper") == "Faster Whisper"

    def test_with_dots(self) -> None:
        """Name with dots should preserve version numbers."""
        assert to_human_name("pyannote-3.1") == "Pyannote 3.1"

    def test_single_word(self) -> None:
        """Single word should title case."""
        assert to_human_name("parakeet") == "Parakeet"


class TestGenerateEngineYaml:
    """Tests for generate_engine_yaml function."""

    @pytest.fixture
    def basic_config(self) -> ScaffoldConfig:
        """Basic scaffold configuration."""
        return ScaffoldConfig(
            engine_id="test-engine",
            stage="transcribe",
            name="Test Engine",
            description="A test engine for testing.",
            gpu="optional",
            memory="4G",
            languages=None,  # All languages
            word_timestamps=True,
            supports_cpu=True,
            min_vram_gb=4,
            min_ram_gb=8,
        )

    def test_generates_valid_yaml(self, basic_config: ScaffoldConfig) -> None:
        """Generated YAML should be valid and parseable."""
        content = generate_engine_yaml(basic_config)
        data = yaml.safe_load(content)

        assert data["schema_version"] == "1.1"
        assert data["id"] == "test-engine"
        assert data["stage"] == "transcribe"
        assert data["name"] == "Test Engine"

    def test_includes_schema_1_1_sections(self, basic_config: ScaffoldConfig) -> None:
        """Generated YAML should include schema 1.1 sections."""
        content = generate_engine_yaml(basic_config)
        data = yaml.safe_load(content)

        assert "hf_compat" in data
        assert "hardware" in data
        assert "performance" in data
        assert data["hf_compat"]["pipeline_tag"] == "automatic-speech-recognition"

    def test_handles_all_languages(self, basic_config: ScaffoldConfig) -> None:
        """Should handle 'all' languages correctly."""
        content = generate_engine_yaml(basic_config)
        data = yaml.safe_load(content)

        assert data["capabilities"]["languages"] == ["all"]

    def test_handles_specific_languages(self, basic_config: ScaffoldConfig) -> None:
        """Should handle specific language list."""
        config = basic_config._replace(languages=["en", "es"])
        content = generate_engine_yaml(config)
        data = yaml.safe_load(content)

        assert data["capabilities"]["languages"] == ["en", "es"]

    def test_gpu_none_config(self, basic_config: ScaffoldConfig) -> None:
        """Should handle GPU none configuration."""
        config = basic_config._replace(gpu="none", supports_cpu=True, min_vram_gb=None)
        content = generate_engine_yaml(config)
        data = yaml.safe_load(content)

        assert data["container"]["gpu"] == "none"
        assert data["hardware"]["supports_cpu"] is True

    def test_correct_pipeline_tags(self) -> None:
        """Should use correct pipeline tag for each stage."""
        for stage in VALID_STAGES:
            config = ScaffoldConfig(
                engine_id=f"test-{stage}",
                stage=stage,
                name=f"Test {stage}",
                description="Test engine for testing.",
                gpu="optional",
                memory="4G",
                languages=None,
                word_timestamps=False,
                supports_cpu=True,
                min_vram_gb=4,
                min_ram_gb=4,
            )
            content = generate_engine_yaml(config)
            data = yaml.safe_load(content)

            expected_tag = PIPELINE_TAG_MAP.get(stage)
            assert data["hf_compat"]["pipeline_tag"] == expected_tag


class TestGenerateEnginePy:
    """Tests for generate_engine_py function."""

    @pytest.fixture
    def basic_config(self) -> ScaffoldConfig:
        """Basic scaffold configuration."""
        return ScaffoldConfig(
            engine_id="test-engine",
            stage="transcribe",
            name="Test Engine",
            description="A test engine for testing.",
            gpu="optional",
            memory="4G",
            languages=None,
            word_timestamps=True,
            supports_cpu=True,
            min_vram_gb=4,
            min_ram_gb=8,
        )

    def test_generates_valid_python(self, basic_config: ScaffoldConfig) -> None:
        """Generated Python should be syntactically valid."""
        content = generate_engine_py(basic_config)

        # Should compile without errors
        compile(content, "<string>", "exec")

    def test_uses_correct_class_name(self, basic_config: ScaffoldConfig) -> None:
        """Should use correct class name derived from engine_id."""
        content = generate_engine_py(basic_config)

        assert "class TestEngineEngine(Engine):" in content

    def test_includes_required_methods(self, basic_config: ScaffoldConfig) -> None:
        """Should include all required Engine methods."""
        content = generate_engine_py(basic_config)

        assert "def process(self, input: TaskInput)" in content
        assert "def health_check(self)" in content
        assert "def get_capabilities(self)" in content

    def test_main_block(self, basic_config: ScaffoldConfig) -> None:
        """Should include main block for running engine."""
        content = generate_engine_py(basic_config)

        assert 'if __name__ == "__main__":' in content
        assert ".run()" in content


class TestGenerateDockerfile:
    """Tests for generate_dockerfile function."""

    @pytest.fixture
    def basic_config(self) -> ScaffoldConfig:
        """Basic scaffold configuration."""
        return ScaffoldConfig(
            engine_id="test-engine",
            stage="transcribe",
            name="Test Engine",
            description="A test engine for testing.",
            gpu="optional",
            memory="4G",
            languages=None,
            word_timestamps=True,
            supports_cpu=True,
            min_vram_gb=4,
            min_ram_gb=8,
        )

    def test_includes_base_image(self, basic_config: ScaffoldConfig) -> None:
        """Should include Python base image."""
        content = generate_dockerfile(basic_config)

        assert "FROM python:3.11-slim" in content

    def test_includes_engine_sdk_install(self, basic_config: ScaffoldConfig) -> None:
        """Should install dalston engine SDK."""
        content = generate_dockerfile(basic_config)

        assert "engine-sdk" in content

    def test_copies_correct_files(self, basic_config: ScaffoldConfig) -> None:
        """Should copy engine files from correct path."""
        content = generate_dockerfile(basic_config)

        assert "engines/transcribe/test-engine/" in content
        assert "engine.yaml" in content
        assert "engine.py" in content

    def test_gpu_required_comment(self) -> None:
        """Should add GPU comment for required GPU."""
        config = ScaffoldConfig(
            engine_id="test-engine",
            stage="diarize",
            name="Test Engine",
            description="A test engine.",
            gpu="required",
            memory="8G",
            languages=None,
            word_timestamps=False,
            supports_cpu=False,
            min_vram_gb=4,
            min_ram_gb=8,
        )
        content = generate_dockerfile(config)

        assert "Requires GPU" in content


class TestGenerateRequirementsTxt:
    """Tests for generate_requirements_txt function."""

    def test_generates_content(self) -> None:
        """Should generate requirements.txt content."""
        config = ScaffoldConfig(
            engine_id="test-engine",
            stage="transcribe",
            name="Test Engine",
            description="A test engine.",
            gpu="optional",
            memory="4G",
            languages=None,
            word_timestamps=True,
            supports_cpu=True,
            min_vram_gb=4,
            min_ram_gb=8,
        )
        content = generate_requirements_txt(config)

        assert len(content) > 0
        assert "# " in content  # Has comments


class TestGenerateReadme:
    """Tests for generate_readme function."""

    @pytest.fixture
    def basic_config(self) -> ScaffoldConfig:
        """Basic scaffold configuration."""
        return ScaffoldConfig(
            engine_id="test-engine",
            stage="transcribe",
            name="Test Engine",
            description="A test engine for testing.",
            gpu="optional",
            memory="4G",
            languages=None,
            word_timestamps=True,
            supports_cpu=True,
            min_vram_gb=4,
            min_ram_gb=8,
        )

    def test_includes_engine_name(self, basic_config: ScaffoldConfig) -> None:
        """Should include engine name as title."""
        content = generate_readme(basic_config)

        assert "# Test Engine" in content

    def test_includes_docker_commands(self, basic_config: ScaffoldConfig) -> None:
        """Should include docker compose commands."""
        content = generate_readme(basic_config)

        assert "docker compose" in content
        assert "test-engine" in content

    def test_includes_hardware_requirements(self, basic_config: ScaffoldConfig) -> None:
        """Should include hardware requirements section."""
        content = generate_readme(basic_config)

        assert "Hardware Requirements" in content
        assert "GPU" in content
        assert "Memory" in content


class TestScaffoldEngine:
    """Tests for scaffold_engine function."""

    def test_dry_run_does_not_create_files(self) -> None:
        """Dry run should not create any files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            engines_dir = Path(tmpdir)

            config = ScaffoldConfig(
                engine_id="test-engine",
                stage="transcribe",
                name="Test Engine",
                description="A test engine.",
                gpu="optional",
                memory="4G",
                languages=None,
                word_timestamps=True,
                supports_cpu=True,
                min_vram_gb=4,
                min_ram_gb=8,
            )

            result = scaffold_engine(config, engines_dir, dry_run=True)

            assert result is True
            assert not (engines_dir / "transcribe" / "test-engine").exists()

    def test_creates_all_files(self) -> None:
        """Should create all required files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            engines_dir = Path(tmpdir)

            config = ScaffoldConfig(
                engine_id="test-engine",
                stage="transcribe",
                name="Test Engine",
                description="A test engine.",
                gpu="optional",
                memory="4G",
                languages=None,
                word_timestamps=True,
                supports_cpu=True,
                min_vram_gb=4,
                min_ram_gb=8,
            )

            result = scaffold_engine(config, engines_dir, dry_run=False)

            assert result is True

            engine_dir = engines_dir / "transcribe" / "test-engine"
            assert engine_dir.exists()
            assert (engine_dir / "engine.yaml").exists()
            assert (engine_dir / "engine.py").exists()
            assert (engine_dir / "Dockerfile").exists()
            assert (engine_dir / "requirements.txt").exists()
            assert (engine_dir / "README.md").exists()

    def test_engine_yaml_is_valid(self) -> None:
        """Created engine.yaml should be valid YAML."""
        with tempfile.TemporaryDirectory() as tmpdir:
            engines_dir = Path(tmpdir)

            config = ScaffoldConfig(
                engine_id="test-engine",
                stage="transcribe",
                name="Test Engine",
                description="A test engine.",
                gpu="optional",
                memory="4G",
                languages=None,
                word_timestamps=True,
                supports_cpu=True,
                min_vram_gb=4,
                min_ram_gb=8,
            )

            scaffold_engine(config, engines_dir, dry_run=False)

            engine_yaml_path = (
                engines_dir / "transcribe" / "test-engine" / "engine.yaml"
            )
            with open(engine_yaml_path) as f:
                data = yaml.safe_load(f)

            assert data["id"] == "test-engine"
            assert data["stage"] == "transcribe"
            assert data["schema_version"] == "1.1"

    def test_fails_if_directory_exists(self) -> None:
        """Should fail if engine directory already exists."""
        with tempfile.TemporaryDirectory() as tmpdir:
            engines_dir = Path(tmpdir)
            existing_dir = engines_dir / "transcribe" / "test-engine"
            existing_dir.mkdir(parents=True)

            config = ScaffoldConfig(
                engine_id="test-engine",
                stage="transcribe",
                name="Test Engine",
                description="A test engine.",
                gpu="optional",
                memory="4G",
                languages=None,
                word_timestamps=True,
                supports_cpu=True,
                min_vram_gb=4,
                min_ram_gb=8,
            )

            result = scaffold_engine(config, engines_dir, dry_run=False)

            assert result is False


class TestValidStages:
    """Tests for VALID_STAGES constant."""

    def test_includes_all_pipeline_stages(self) -> None:
        """Should include all expected pipeline stages."""
        expected = [
            "prepare",
            "transcribe",
            "align",
            "diarize",
            "detect",
            "redact",
            "refine",
            "merge",
        ]
        assert VALID_STAGES == expected

    def test_pipeline_tag_map_covers_all_stages(self) -> None:
        """PIPELINE_TAG_MAP should have entry for each stage."""
        for stage in VALID_STAGES:
            assert stage in PIPELINE_TAG_MAP
