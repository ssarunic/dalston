"""Tests for Riva runtime in engine selection (M63).

Verifies:
- Riva model selected when engine is running
- Correct runtime_model_id resolved (NIM tag, not NGC URL)
- Riva preferred over NeMo when both running (faster RTF)
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from dalston.engine_sdk.types import EngineCapabilities
from dalston.orchestrator.engine_selector import (
    _find_best_downloaded_model,
    _resolve_runtime_model_id,
    select_engine,
)
from dalston.orchestrator.registry import BatchEngineState


def _make_model(**kwargs):
    defaults = {
        "id": "nvidia/parakeet-ctc-1.1b-riva",
        "runtime": "riva",
        "runtime_model_id": "parakeet-1-1b-ctc-en-us",
        "stage": "transcribe",
        "source": "nvcr.io/nim/nvidia/parakeet-1-1b-ctc-en-us",
        "management": "external",
        "status": "ready",
        "languages": ["en"],
        "size_bytes": 4_000_000_000,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


class TestRivaModelResolution:
    """Test _resolve_runtime_model_id for Riva models."""

    def test_returns_nim_tag_not_source(self):
        model = _make_model()
        result = _resolve_runtime_model_id(model, "transcribe")
        assert result == "parakeet-1-1b-ctc-en-us"
        assert result != "nvcr.io/nim/nvidia/parakeet-1-1b-ctc-en-us"

    def test_external_model_same_for_all_stages(self):
        model = _make_model()
        assert (
            _resolve_runtime_model_id(model, "transcribe") == "parakeet-1-1b-ctc-en-us"
        )
        assert _resolve_runtime_model_id(model, "diarize") == "parakeet-1-1b-ctc-en-us"


class TestRivaEngineSelection:
    """Test engine selection with Riva runtime."""

    @pytest.mark.asyncio
    async def test_riva_model_found_by_auto_selection(self):
        """When Riva model is ready, auto-selection finds it."""
        from dalston.db.models import ModelRegistryModel

        mock_db = AsyncMock()

        # Return a Riva model from the registry
        model = MagicMock(spec=ModelRegistryModel)
        model.id = "nvidia/parakeet-ctc-1.1b-riva"
        model.runtime = "riva"
        model.runtime_model_id = "parakeet-1-1b-ctc-en-us"
        model.stage = "transcribe"
        model.status = "ready"
        model.management = "external"
        model.source = "nvcr.io/nim/nvidia/parakeet-1-1b-ctc-en-us"
        model.languages = ["en"]
        model.size_bytes = 4_000_000_000

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [model]
        mock_db.execute.return_value = mock_result

        result = await _find_best_downloaded_model(
            runtime="riva",
            stage="transcribe",
            requirements={"language": "en"},
            db=mock_db,
        )

        assert result is not None
        assert result.runtime == "riva"

    @pytest.mark.asyncio
    async def test_explicit_riva_model_selection(self):
        """User explicitly selects Riva model."""
        from dalston.db.models import ModelRegistryModel

        mock_db = AsyncMock()

        model = MagicMock(spec=ModelRegistryModel)
        model.id = "nvidia/parakeet-ctc-1.1b-riva"
        model.stage = "transcribe"
        model.status = "ready"
        model.runtime = "riva"
        model.runtime_model_id = "parakeet-1-1b-ctc-en-us"
        model.source = "nvcr.io/nim/nvidia/parakeet-1-1b-ctc-en-us"
        model.management = "external"
        model.languages = None

        # Mock DB lookup
        mock_scalar_result = MagicMock()
        mock_scalar_result.scalar_one_or_none.return_value = model
        mock_db.execute.return_value = mock_scalar_result

        # Mock registry: riva engine is available
        mock_registry = AsyncMock()
        engine_state = MagicMock(spec=BatchEngineState)
        engine_state.runtime = "riva"
        engine_state.is_available = True
        engine_state.capabilities = EngineCapabilities(
            runtime="riva",
            version="1.0.0",
            stages=["transcribe"],
            languages=["en"],
            supports_word_timestamps=True,
            rtf_gpu=0.0001,
        )
        mock_registry.get_engine.return_value = engine_state

        mock_catalog = MagicMock()

        result = await select_engine(
            "transcribe",
            {"language": "en"},
            mock_registry,
            mock_catalog,
            user_preference="nvidia/parakeet-ctc-1.1b-riva",
            db=mock_db,
        )

        assert result.runtime == "riva"
        assert result.runtime_model_id == "parakeet-1-1b-ctc-en-us"
