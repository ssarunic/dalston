"""Test helpers for DAG builder tests.

Provides a sync test wrapper around the async DAG builder for use in
unit and integration tests that don't need the full registry/catalog.
"""

from uuid import UUID

from dalston.orchestrator.dag import (
    DEFAULT_ENGINES,
    _build_dag_with_engines,
)


def build_task_dag_for_test(
    job_id: UUID,
    audio_uri: str,
    parameters: dict,
) -> list:
    """Test helper that wraps _build_dag_with_engines with sensible defaults.

    This simulates the old sync build_task_dag API for tests that don't
    need the full async registry/catalog infrastructure.

    For tests involving actual model resolution and engine selection,
    use the async build_task_dag with mocked registry/catalog
    (see test_capability_driven_dag.py for examples).

    Args:
        job_id: Job UUID
        audio_uri: S3 URI to audio file
        parameters: Job parameters dict

    Returns:
        List of Task objects with dependencies wired
    """
    # Start with default engines
    engines = dict(DEFAULT_ENGINES)

    # Handle engine overrides from parameters
    for stage in ["transcribe", "align", "diarize", "prepare", "merge"]:
        override_key = f"engine_{stage}"
        if override_key in parameters:
            engines[stage] = parameters[override_key]

    # Determine runtime_model_id from engine_transcribe override
    # In real usage, this comes from EngineSelectionResult after catalog lookup
    runtime_model_id = None
    transcribe_engine = engines.get("transcribe", DEFAULT_ENGINES["transcribe"])

    # For testing: map known model IDs to their runtime + runtime_model_id
    # This simulates what the engine_selector does with the catalog
    MODEL_TO_RUNTIME = {
        "parakeet-tdt-1.1b": ("nemo", "nvidia/parakeet-tdt-1.1b"),
        "faster-whisper-large-v3-turbo": ("faster-whisper", None),
        "faster-whisper-base": ("faster-whisper", None),
    }

    if transcribe_engine in MODEL_TO_RUNTIME:
        runtime, runtime_model_id = MODEL_TO_RUNTIME[transcribe_engine]
        engines["transcribe"] = runtime

    # Determine skip flags based on parameters and engine capabilities
    # NeMo models have native word timestamps, so skip alignment
    skip_alignment = False
    if engines.get("transcribe") == "nemo":
        skip_alignment = True
    elif parameters.get("timestamps_granularity") == "segment":
        skip_alignment = True

    # Skip diarization if not requested
    skip_diarization = parameters.get("speaker_detection") not in ("diarize",)

    return _build_dag_with_engines(
        job_id=job_id,
        audio_uri=audio_uri,
        parameters=parameters,
        engines=engines,
        skip_alignment=skip_alignment,
        skip_diarization=skip_diarization,
        runtime_model_id=runtime_model_id,
    )
