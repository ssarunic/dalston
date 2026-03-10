"""Tests for console model display resolution (auto vs explicit)."""

from types import SimpleNamespace

from dalston.gateway.services.console import ConsoleService


def _make_task(stage: str, runtime: str = "", config: dict | None = None):
    return SimpleNamespace(
        stage=stage,
        runtime=runtime,
        config=config or {},
    )


def _make_job(
    *,
    parameters: dict | None = None,
    tasks: list | None = None,
    status: str = "completed",
):
    return SimpleNamespace(
        parameters=parameters or {},
        tasks=tasks or [],
        status=status,
    )


class TestResolveModelDisplay:
    """Tests for ConsoleService._resolve_model_display."""

    def test_explicit_model_returns_as_is(self):
        job = _make_job(parameters={"engine_transcribe": "whisper-large-v3"})

        result = ConsoleService._resolve_model_display(job)

        assert result == "whisper-large-v3"

    def test_auto_with_runtime_model_id(self):
        task = _make_task(
            "transcribe",
            runtime="faster-whisper-base",
            config={"runtime_model_id": "faster-whisper-large-v3"},
        )
        job = _make_job(tasks=[task])

        result = ConsoleService._resolve_model_display(job)

        assert result == "Auto (faster-whisper-large-v3)"

    def test_auto_with_task_runtime_fallback(self):
        task = _make_task("transcribe", runtime="faster-whisper-base")
        job = _make_job(tasks=[task])

        result = ConsoleService._resolve_model_display(job)

        assert result == "Auto (faster-whisper-base)"

    def test_auto_pending_selection_when_running(self):
        job = _make_job(status="running")

        result = ConsoleService._resolve_model_display(job)

        assert result == "Auto (pending selection)"

    def test_auto_pending_selection_when_pending(self):
        job = _make_job(status="pending")

        result = ConsoleService._resolve_model_display(job)

        assert result == "Auto (pending selection)"

    def test_auto_fallback_when_completed_no_tasks(self):
        job = _make_job(status="completed")

        result = ConsoleService._resolve_model_display(job)

        assert result == "Auto"

    def test_auto_fallback_when_failed_no_tasks(self):
        job = _make_job(status="failed")

        result = ConsoleService._resolve_model_display(job)

        assert result == "Auto"

    def test_per_channel_transcribe_task(self):
        task = _make_task("transcribe_ch0", runtime="parakeet-0.6b")
        job = _make_job(tasks=[task])

        result = ConsoleService._resolve_model_display(job)

        assert result == "Auto (parakeet-0.6b)"

    def test_prefers_transcribe_over_transcribe_ch(self):
        ch_task = _make_task("transcribe_ch0", runtime="parakeet-0.6b")
        main_task = _make_task(
            "transcribe",
            runtime="faster-whisper-base",
            config={"runtime_model_id": "faster-whisper-large-v3"},
        )
        job = _make_job(tasks=[ch_task, main_task])

        result = ConsoleService._resolve_model_display(job)

        assert result == "Auto (faster-whisper-large-v3)"

    def test_runtime_model_id_preferred_over_runtime(self):
        task = _make_task(
            "transcribe",
            runtime="generic-runtime",
            config={"runtime_model_id": "specific-model"},
        )
        job = _make_job(tasks=[task])

        result = ConsoleService._resolve_model_display(job)

        assert result == "Auto (specific-model)"

    def test_none_parameters_treated_as_auto(self):
        job = _make_job(parameters=None, status="completed")

        result = ConsoleService._resolve_model_display(job)

        assert result == "Auto"

    def test_empty_engine_transcribe_treated_as_auto(self):
        task = _make_task("transcribe", runtime="fw-base")
        job = _make_job(parameters={"engine_transcribe": ""}, tasks=[task])

        result = ConsoleService._resolve_model_display(job)

        assert result == "Auto (fw-base)"

    def test_non_transcribe_tasks_ignored(self):
        task = _make_task("diarize", runtime="pyannote-3.1")
        job = _make_job(tasks=[task], status="completed")

        result = ConsoleService._resolve_model_display(job)

        assert result == "Auto"
