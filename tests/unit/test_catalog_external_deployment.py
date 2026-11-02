"""M104: ``deployment: external`` flows from engine.yaml into the catalog.

External engines (e.g. the FluidAudio Mac worker) run outside the compose
stack.  They must stay in the generated catalog so ``/v1/engines``, the
console and scheduler timeouts see them, but they are exempt from the
catalog<->compose contract check and can never be ``on_demand``.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "scripts"))

from generate_catalog import transform_engine_id_to_entry  # noqa: E402

from dalston.orchestrator.catalog import EngineCatalog


def _engine_yaml(**overrides: object) -> dict:
    data: dict = {
        "schema_version": "1.1",
        "id": "stt-transcribe-ext",
        "engine_id": "ext",
        "stage": "transcribe",
        "name": "External Test Engine",
        "version": "1.0.0",
        "description": "Runs outside compose.",
        "container": {"gpu": "none", "memory": "1G"},
        "capabilities": {"word_timestamps": True, "max_concurrency": 1},
        "hardware": {"supports_cpu": True, "min_ram_gb": 4},
        "performance": {"rtf_gpu": 0.3, "rtf_cpu": 0.3},
    }
    data.update(overrides)
    return data


class TestGenerateCatalogDeployment:
    def test_defaults_to_compose(self) -> None:
        entry = transform_engine_id_to_entry(_engine_yaml(), Path("engine.yaml"))
        assert entry["deployment"] == "compose"

    def test_external_passes_through(self) -> None:
        entry = transform_engine_id_to_entry(
            _engine_yaml(deployment="external"), Path("engine.yaml")
        )
        assert entry["deployment"] == "external"
        assert entry["on_demand"] is False

    def test_unknown_value_rejected(self) -> None:
        with pytest.raises(ValueError, match="Invalid deployment"):
            transform_engine_id_to_entry(
                _engine_yaml(deployment="laptop"), Path("engine.yaml")
            )

    def test_external_and_on_demand_conflict(self) -> None:
        with pytest.raises(ValueError, match="cannot be combined"):
            transform_engine_id_to_entry(
                _engine_yaml(deployment="external", on_demand=True),
                Path("engine.yaml"),
            )


class TestCatalogEntryDeployment:
    def _entry_data(self, **overrides: object) -> dict:
        data: dict = {
            "version": "1.0.0",
            "capabilities": {"stages": ["transcribe"]},
            "hardware": {},
            "performance": {},
        }
        data.update(overrides)
        return data

    def test_defaults_to_compose(self) -> None:
        entry = EngineCatalog._parse_engine_entry("ext", self._entry_data())
        assert entry.deployment == "compose"

    def test_external_forces_on_demand_false(self) -> None:
        # A stale or hand-edited catalog claiming on_demand for an external
        # engine must not make the selector enqueue against a Mac that no
        # autoscaler can launch.
        entry = EngineCatalog._parse_engine_entry(
            "ext", self._entry_data(deployment="external", on_demand=True)
        )
        assert entry.deployment == "external"
        assert entry.on_demand is False

    def test_compose_keeps_on_demand(self) -> None:
        entry = EngineCatalog._parse_engine_entry(
            "ext", self._entry_data(deployment="compose", on_demand=True)
        )
        assert entry.on_demand is True

    def test_unknown_value_rejected(self) -> None:
        with pytest.raises(ValueError, match="deployment must be one of"):
            EngineCatalog._parse_engine_entry(
                "ext", self._entry_data(deployment="laptop")
            )
