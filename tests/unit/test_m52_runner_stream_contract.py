"""M52 tests for engine runner stream polling contract."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import dalston.engine_sdk.runner as runner_module
from dalston.engine_sdk.base import Engine
from dalston.engine_sdk.context import BatchTaskContext
from dalston.engine_sdk.runner import EngineRunner
from dalston.engine_sdk.types import EngineInput, EngineOutput


class _NoopEngine(Engine):
    def process(
        self,
        input: EngineInput,
        ctx: BatchTaskContext,
    ) -> EngineOutput:
        del input
        del ctx
        return EngineOutput(data={})


def test_runner_has_no_legacy_stage_fallback_method() -> None:
    assert not hasattr(EngineRunner, "_candidate_stream_ids")


def test_runner_polls_runtime_stream_only(monkeypatch) -> None:
    with patch.dict(os.environ, {"DALSTON_RUNTIME": "runtime-only"}):
        runner = EngineRunner(_NoopEngine())
    runner._redis = MagicMock()
    runner._stage = "transcribe"

    seen_stages: list[str] = []

    def fake_claim_stale(*, stage: str, **kwargs):
        del kwargs
        seen_stages.append(stage)
        return []

    def fake_read_task(*, stage: str, **kwargs):
        del kwargs
        seen_stages.append(stage)
        return None

    monkeypatch.setattr(
        runner_module,
        "claim_stale_from_dead_engines",
        lambda redis_client, stage, consumer, min_idle_ms, count: fake_claim_stale(
            stage=stage,
            redis_client=redis_client,
            consumer=consumer,
            min_idle_ms=min_idle_ms,
            count=count,
        ),
    )
    monkeypatch.setattr(
        runner_module,
        "read_task",
        lambda redis_client, stage, consumer, block_ms: fake_read_task(
            stage=stage,
            redis_client=redis_client,
            consumer=consumer,
            block_ms=block_ms,
        ),
    )

    runner._poll_and_process()

    assert seen_stages == ["runtime-only", "runtime-only"]
