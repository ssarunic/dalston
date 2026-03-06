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

    # Determine runtime_model_id values by stage.
    # In real usage, these come from EngineSelectionResult after registry lookup.
    stage_runtime_model_ids: dict[str, str] = {}
    transcribe_engine = engines.get("transcribe", DEFAULT_ENGINES["transcribe"])

    # For testing: map known model IDs to their runtime + runtime_model_id
    # This simulates what the engine_selector does with the catalog
    # Supports both catalog model IDs (parakeet-onnx-*) and runtime model IDs (nvidia/*)
    MODEL_TO_RUNTIME = {
        # NeMo models (GPU required) - runtime model IDs
        "nvidia/parakeet-tdt-1.1b": ("nemo", "nvidia/parakeet-tdt-1.1b"),
        # NeMo ONNX models (CPU/GPU) - runtime model IDs
        "nvidia/parakeet-ctc-0.6b": ("nemo-onnx", "nvidia/parakeet-ctc-0.6b"),
        "nvidia/parakeet-ctc-1.1b": ("nemo-onnx", "nvidia/parakeet-ctc-1.1b"),
        "nvidia/parakeet-tdt-0.6b-v2": ("nemo-onnx", "nvidia/parakeet-tdt-0.6b-v2"),
        "nvidia/parakeet-tdt-0.6b-v3": ("nemo-onnx", "nvidia/parakeet-tdt-0.6b-v3"),
        "nvidia/parakeet-rnnt-0.6b": ("nemo-onnx", "nvidia/parakeet-rnnt-0.6b"),
        # NeMo ONNX models (CPU/GPU) - catalog model IDs
        "parakeet-onnx-ctc-0.6b": ("nemo-onnx", "nvidia/parakeet-ctc-0.6b"),
        "parakeet-onnx-ctc-1.1b": ("nemo-onnx", "nvidia/parakeet-ctc-1.1b"),
        "parakeet-onnx-tdt-0.6b-v2": ("nemo-onnx", "nvidia/parakeet-tdt-0.6b-v2"),
        "parakeet-onnx-tdt-0.6b-v3": ("nemo-onnx", "nvidia/parakeet-tdt-0.6b-v3"),
        "parakeet-onnx-rnnt-0.6b": ("nemo-onnx", "nvidia/parakeet-rnnt-0.6b"),
        # Faster Whisper models
        "Systran/faster-whisper-large-v3-turbo": ("faster-whisper", "large-v3-turbo"),
        "Systran/faster-whisper-base": ("faster-whisper", "base"),
    }

    if transcribe_engine in MODEL_TO_RUNTIME:
        runtime, runtime_model_id = MODEL_TO_RUNTIME[transcribe_engine]
        engines["transcribe"] = runtime
        stage_runtime_model_ids["transcribe"] = runtime_model_id

    if parameters.get("model_diarize"):
        stage_runtime_model_ids["diarize"] = parameters["model_diarize"]
    if parameters.get("model_align"):
        stage_runtime_model_ids["align"] = parameters["model_align"]
    if parameters.get("model_pii_detect"):
        stage_runtime_model_ids["pii_detect"] = parameters["model_pii_detect"]

    # Determine skip flags based on parameters and engine capabilities
    # NeMo and NeMo-ONNX models have native word timestamps, so skip alignment
    skip_alignment = False
    if engines.get("transcribe") in ("nemo", "nemo-onnx"):
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
        runtime_model_id=stage_runtime_model_ids.get("transcribe"),
        stage_runtime_model_ids=stage_runtime_model_ids,
    )
