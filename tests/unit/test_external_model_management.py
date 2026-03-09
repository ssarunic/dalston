"""Tests for external model management (M63).

External models (management="external") are managed outside Dalston
(e.g., Riva NIM containers). They should be:
- Seeded as status="ready" (not "not_downloaded")
- Rejected from pull_model() with a clear error
- Resolved with runtime_model_id directly (not source)
- Skipped during S3 sync
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from dalston.db.models import ModelRegistryModel
from dalston.gateway.services.model_yaml_loader import _load_single_yaml
from dalston.orchestrator.engine_selector import _resolve_runtime_model_id


class TestModelYAMLLoaderManagement:
    """Test management field parsing in YAML loader."""

    def test_default_management_is_dalston(self, tmp_path):
        yaml_file = tmp_path / "test.yaml"
        yaml_file.write_text(
            "id: test/model\nruntime: nemo\nruntime_model_id: test/model\nname: Test\n"
        )
        entry = _load_single_yaml(yaml_file)
        assert entry.management == "dalston"

    def test_external_management_parsed(self, tmp_path):
        yaml_file = tmp_path / "test.yaml"
        yaml_file.write_text(
            "id: test/riva-model\n"
            "runtime: riva\n"
            "runtime_model_id: parakeet-1-1b-ctc-en-us\n"
            "name: Riva Model\n"
            "management: external\n"
        )
        entry = _load_single_yaml(yaml_file)
        assert entry.management == "external"


class TestResolveRuntimeModelId:
    """Test _resolve_runtime_model_id with external models."""

    def _make_model(self, **kwargs) -> ModelRegistryModel:
        defaults = {
            "id": "nvidia/parakeet-ctc-1.1b-riva",
            "runtime": "riva",
            "runtime_model_id": "parakeet-1-1b-ctc-en-us",
            "stage": "transcribe",
            "source": "nvcr.io/nim/nvidia/parakeet-1-1b-ctc-en-us",
            "management": "external",
        }
        defaults.update(kwargs)
        model = MagicMock(spec=ModelRegistryModel)
        for k, v in defaults.items():
            setattr(model, k, v)
        return model

    def test_external_model_returns_runtime_model_id(self):
        model = self._make_model()
        result = _resolve_runtime_model_id(model, "transcribe")
        assert result == "parakeet-1-1b-ctc-en-us"

    def test_external_model_ignores_source(self):
        """Source is informational for external models, not used for resolution."""
        model = self._make_model(
            source="nvcr.io/nim/nvidia/parakeet-1-1b-ctc-en-us",
        )
        result = _resolve_runtime_model_id(model, "transcribe")
        # Should NOT return the NGC URL
        assert result == "parakeet-1-1b-ctc-en-us"

    def test_dalston_model_uses_source_for_transcribe(self):
        model = self._make_model(
            management="dalston",
            source="nvidia/parakeet-ctc-1.1b",
            runtime_model_id="nvidia/parakeet-ctc-1.1b",
        )
        result = _resolve_runtime_model_id(model, "transcribe")
        assert result == "nvidia/parakeet-ctc-1.1b"

    def test_dalston_model_uses_runtime_model_id_for_other_stages(self):
        model = self._make_model(
            management="dalston",
            source="some-source",
            runtime_model_id="my-runtime-id",
        )
        result = _resolve_runtime_model_id(model, "diarize")
        assert result == "my-runtime-id"


class TestSeedExternalModels:
    """Test that seed_from_yamls seeds external models as ready."""

    @pytest.mark.asyncio
    async def test_external_model_seeded_as_ready(self):
        from dalston.gateway.services.model_registry import ModelRegistryService

        service = ModelRegistryService()
        mock_db = AsyncMock()
        mock_db.add = MagicMock()  # db.add() is synchronous

        # Mock: model doesn't exist yet
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_result

        # Create a temp directory with an external model YAML
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmpdir:
            yaml_path = Path(tmpdir) / "riva-model.yaml"
            yaml_path.write_text(
                "id: nvidia/parakeet-ctc-1.1b-riva\n"
                "runtime: riva\n"
                "runtime_model_id: parakeet-1-1b-ctc-en-us\n"
                "name: Riva Model\n"
                "stage: transcribe\n"
                "management: external\n"
                "source: nvcr.io/nim/nvidia/parakeet-1-1b-ctc-en-us\n"
                "languages:\n"
                "  - en\n"
            )

            result = await service.seed_from_yamls(mock_db, models_dir=Path(tmpdir))

        assert result["created"] == 1

        # Verify the model was added with correct attributes
        add_call = mock_db.add.call_args_list[0]
        model = add_call[0][0]
        assert isinstance(model, ModelRegistryModel)
        assert model.status == "ready"
        assert model.management == "external"


class TestPullModelGuard:
    """Test that pull_model rejects external models."""

    @pytest.mark.asyncio
    async def test_pull_external_model_raises(self):
        from dalston.gateway.services.model_registry import ModelRegistryService

        service = ModelRegistryService()
        mock_db = AsyncMock()

        # Mock: model exists and is external
        model = MagicMock(spec=ModelRegistryModel)
        model.id = "nvidia/parakeet-ctc-1.1b-riva"
        model.management = "external"
        model.status = "ready"

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = model
        mock_db.execute.return_value = mock_result

        with pytest.raises(ValueError, match="externally managed"):
            await service.pull_model(mock_db, "nvidia/parakeet-ctc-1.1b-riva")
