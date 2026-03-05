"""Phase-3 regression tests for produced-artifact declarations."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import patch

from dalston.common.artifacts import MaterializedArtifact
from dalston.engine_sdk.context import BatchTaskContext
from dalston.engine_sdk.types import TaskInput


def _ctx(task_id: str = "task-123", job_id: str = "job-456") -> BatchTaskContext:
    return BatchTaskContext(
        runtime="test-runtime",
        instance="test-instance",
        task_id=task_id,
        job_id=job_id,
        stage="test-stage",
    )


def _load_engine_class(module_name: str, file_path: str, class_name: str):
    spec = importlib.util.spec_from_file_location(module_name, Path(file_path))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load engine from {file_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return getattr(module, class_name)


def test_prepare_engine_declares_prepared_audio_artifact(tmp_path: Path) -> None:
    AudioPrepareEngine = _load_engine_class(
        "m51_prepare_engine",
        "engines/stt-prepare/audio-prepare/engine.py",
        "AudioPrepareEngine",
    )

    source = tmp_path / "in.wav"
    source.write_bytes(b"raw")

    with patch.object(
        AudioPrepareEngine, "_verify_ffmpeg_installed", return_value=None
    ):
        engine = AudioPrepareEngine()
    engine._verify_ffmpeg_installed = lambda: None  # type: ignore[method-assign]
    engine._probe_audio = lambda _: {  # type: ignore[method-assign]
        "duration": 3.0,
        "sample_rate": 16000,
        "channels": 1,
        "bit_depth": 16,
    }
    engine._convert_audio = lambda **_: (tmp_path / "prepared.wav").write_bytes(  # type: ignore[method-assign]
        b"prepared"
    )

    task_input = TaskInput(
        task_id="task-prepare",
        job_id="job-1",
        stage="prepare",
        materialized_artifacts={
            "audio": MaterializedArtifact(
                artifact_id="job-1:source:audio",
                kind="audio",
                local_path=source,
            )
        },
        config={},
    )

    output = engine.process(task_input, _ctx(task_id="task-prepare", job_id="job-1"))

    assert output.data.channel_files[0].artifact_id == "task-prepare:prepared_audio"
    assert output.produced_artifacts[0].logical_name == "prepared_audio"
    assert output.produced_artifacts[0].local_path.exists()


def test_audio_redactor_declares_redacted_artifact_id(tmp_path: Path) -> None:
    AudioRedactionEngine = _load_engine_class(
        "m51_redactor_engine",
        "engines/stt-redact/audio-redactor/engine.py",
        "AudioRedactionEngine",
    )

    source = tmp_path / "input.wav"
    source.write_bytes(b"mono-audio")

    engine = AudioRedactionEngine()
    task_input = TaskInput(
        task_id="task-redact",
        job_id="job-2",
        stage="audio_redact",
        materialized_artifacts={
            "audio": MaterializedArtifact(
                artifact_id="task-prepare:prepared_audio",
                kind="audio",
                local_path=source,
            )
        },
        previous_outputs={"pii_detect": {"entities": []}},
        config={"redaction_mode": "silence", "buffer_ms": 25},
    )

    output = engine.process(task_input, _ctx(task_id="task-redact", job_id="job-2"))

    assert output.data.redacted_audio_artifact_id == "task-redact:redacted_audio"
    assert output.produced_artifacts[0].logical_name == "redacted_audio"
    assert output.produced_artifacts[0].local_path.exists()


def test_merge_engine_declares_transcript_artifact(tmp_path: Path) -> None:
    FinalMergerEngine = _load_engine_class(
        "m51_merge_engine",
        "engines/stt-merge/final-merger/engine.py",
        "FinalMergerEngine",
    )

    input_audio = tmp_path / "audio.wav"
    input_audio.write_bytes(b"audio")

    engine = FinalMergerEngine()
    task_input = TaskInput(
        task_id="task-merge",
        job_id="job-3",
        stage="merge",
        materialized_artifacts={
            "audio": MaterializedArtifact(
                artifact_id="task-prepare:prepared_audio",
                kind="audio",
                local_path=input_audio,
            )
        },
        previous_outputs={
            "prepare": {
                "channel_files": [
                    {
                        "artifact_id": "task-prepare:prepared_audio",
                        "format": "wav",
                        "duration": 5.0,
                        "sample_rate": 16000,
                        "channels": 1,
                        "bit_depth": 16,
                    }
                ],
                "split_channels": False,
                "runtime": "audio-prepare",
            },
            "transcribe": {
                "segments": [{"start": 0.0, "end": 0.8, "text": "hello"}],
                "text": "hello",
                "language": "en",
                "runtime": "faster-whisper",
            },
        },
        config={"speaker_detection": "none"},
    )

    output = engine.process(task_input, _ctx(task_id="task-merge", job_id="job-3"))

    assert output.produced_artifacts[-1].logical_name == "transcript"
    assert output.produced_artifacts[-1].local_path.exists()
    assert output.data.job_id == "job-3"
