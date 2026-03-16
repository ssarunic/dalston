"""Unit tests for faster-whisper engine temperature passthrough."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

from dalston.engine_sdk import TaskRequest
from dalston.engine_sdk.context import BatchTaskContext


def _ctx(task_id: str, job_id: str) -> BatchTaskContext:
    return BatchTaskContext(
        engine_id="test-engine_id",
        instance="test-instance",
        task_id=task_id,
        job_id=job_id,
        stage="transcribe",
    )


def _load_whisper_engine():
    engine_path = Path("engines/stt-unified/faster-whisper/batch_engine.py")
    spec = importlib.util.spec_from_file_location("m61_whisper_engine", engine_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["m61_whisper_engine"] = module
    spec.loader.exec_module(module)
    return module.FasterWhisperBatchEngine


def test_temperature_list_is_forwarded_to_faster_whisper_decoder() -> None:
    FasterWhisperBatchEngine = _load_whisper_engine()
    engine = FasterWhisperBatchEngine()

    mock_segment = SimpleNamespace(
        start=0.0,
        end=1.0,
        text="hello",
        words=[],
        tokens=None,
        avg_logprob=None,
        compression_ratio=None,
        no_speech_prob=None,
    )
    mock_info = SimpleNamespace(language="en", language_probability=0.99, duration=1.0)
    mock_model = MagicMock()
    mock_model.transcribe.return_value = (iter([mock_segment]), mock_info)

    mock_manager = MagicMock()
    mock_manager.acquire.return_value = mock_model
    # M63: Engine now delegates to FasterWhisperInference which owns the manager
    engine._core._manager = mock_manager

    task_id = str(uuid4())
    job_id = str(uuid4())
    output = engine.process(
        TaskRequest(
            task_id=task_id,
            job_id=job_id,
            audio_path=Path("/tmp/test.wav"),
            config={"temperature": [0.0, 0.2, 0.4]},
        ),
        _ctx(task_id, job_id),
    )

    call_kwargs = mock_model.transcribe.call_args.kwargs
    assert call_kwargs["temperature"] == [0.0, 0.2, 0.4]
    assert output.data.segments[0].metadata["temperature"] == 0.0
