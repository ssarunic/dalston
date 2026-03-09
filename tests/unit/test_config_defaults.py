from dalston.config import Settings


def test_lite_mode_retention_default_is_zero(monkeypatch) -> None:
    monkeypatch.delenv("DALSTON_RETENTION_DEFAULT_DAYS", raising=False)
    settings = Settings(runtime_mode="lite", _env_file=None)
    assert settings.retention_default_days == 0


def test_distributed_mode_retention_default_remains_thirty(monkeypatch) -> None:
    monkeypatch.delenv("DALSTON_RETENTION_DEFAULT_DAYS", raising=False)
    settings = Settings(runtime_mode="distributed", _env_file=None)
    assert settings.retention_default_days == 30


def test_lite_mode_keeps_explicit_retention_override() -> None:
    settings = Settings(
        runtime_mode="lite",
        retention_default_days=7,
        _env_file=None,
    )
    assert settings.retention_default_days == 7
