"""Phase-6 static guardrails for M51 stateless engine contract."""

from __future__ import annotations

from pathlib import Path

ENGINE_ROOT = Path("engines")
ENGINE_RUNTIME_FILES = sorted(
    file_path
    for file_path in ENGINE_ROOT.glob("**/engine.py")
    if "stt-rt" not in file_path.parts
)

FORBIDDEN_IMPORT_PATTERNS = (
    "from dalston.engine_sdk import io",
    "dalston.engine_sdk.io",
    "import boto3",
    "from boto3",
    "import redis",
    "from redis",
)

FORBIDDEN_CALL_PATTERNS = (
    "build_task_input_uri(",
    "build_task_output_uri(",
    "parse_s3_uri(",
)


def test_runtime_engines_have_new_process_signature() -> None:
    missing = []
    optional_ctx = []
    for file_path in ENGINE_RUNTIME_FILES:
        text = file_path.read_text(encoding="utf-8")
        has_signature = (
            "def process(" in text
            and "input: EngineInput" in text
            and "ctx: BatchTaskContext" in text
            and "-> EngineOutput" in text
        )
        if not has_signature:
            missing.append(str(file_path))
        if "BatchTaskContext | None" in text:
            optional_ctx.append(str(file_path))
    assert not missing, f"Engines missing M51 process signature: {missing}"
    assert not optional_ctx, (
        f"Engines still using optional ctx signature: {optional_ctx}"
    )


def test_runtime_engines_do_not_import_storage_clients_or_helpers() -> None:
    offenders: list[str] = []
    for file_path in ENGINE_RUNTIME_FILES:
        text = file_path.read_text(encoding="utf-8")
        if any(pattern in text for pattern in FORBIDDEN_IMPORT_PATTERNS):
            offenders.append(str(file_path))
    assert not offenders, f"Forbidden runtime imports in engine modules: {offenders}"


def test_runtime_engines_have_no_uri_literals_or_uri_helpers() -> None:
    offenders: list[str] = []
    for file_path in ENGINE_RUNTIME_FILES:
        text = file_path.read_text(encoding="utf-8")
        if "s3://" in text or any(
            pattern in text for pattern in FORBIDDEN_CALL_PATTERNS
        ):
            offenders.append(str(file_path))
    assert not offenders, f"URI coupling remains in engine runtime files: {offenders}"
