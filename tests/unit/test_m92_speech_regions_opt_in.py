"""Prepare-stage speech-region detection is opt-in (debug flag).

The Silero pass costs ~30s per hour of audio on CPU ahead of
transcription, so the DAG only requests it when
DALSTON_PREPARE_SPEECH_REGIONS is set. The prepare engine capability and
the assembler's missed-speech coverage check stay dormant otherwise.
"""

from uuid import uuid4

from tests.dag_test_helpers import build_task_dag_for_test

_AUDIO = "s3://test-bucket/audio/test.wav"


def _prepare_config(monkeypatch, env_value: str | None) -> dict:
    if env_value is None:
        monkeypatch.delenv("DALSTON_PREPARE_SPEECH_REGIONS", raising=False)
    else:
        monkeypatch.setenv("DALSTON_PREPARE_SPEECH_REGIONS", env_value)
    tasks = build_task_dag_for_test(uuid4(), _AUDIO, {})
    return next(t for t in tasks if t.stage == "prepare").config


class TestSpeechRegionsOptIn:
    def test_off_by_default(self, monkeypatch):
        config = _prepare_config(monkeypatch, None)
        assert "detect_speech_regions" not in config

    def test_enabled_via_env(self, monkeypatch):
        config = _prepare_config(monkeypatch, "1")
        assert config["detect_speech_regions"] is True

    def test_disabled_values_stay_off(self, monkeypatch):
        for value in ("0", "false", "off", ""):
            config = _prepare_config(monkeypatch, value)
            assert "detect_speech_regions" not in config
