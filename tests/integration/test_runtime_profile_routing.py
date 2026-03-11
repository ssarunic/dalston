from __future__ import annotations

from dalston.orchestrator.catalog import EngineCatalog


def test_generated_catalog_contains_migrated_execution_profiles() -> None:
    catalog = EngineCatalog.load()

    assert catalog.get_engine("audio-prepare").execution_profile == "container"
    assert catalog.get_engine("nemo-msdd").execution_profile == "container"
    assert catalog.get_engine("faster-whisper").execution_profile == "container"
