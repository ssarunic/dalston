"""Lite mode orchestrator with in-memory queue for scoped batch flow."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from uuid import uuid4

from dalston.common.queue_backends import InMemoryQueue
from dalston.config import get_settings
from dalston.db.session import init_db
from dalston.gateway.services.artifact_store import LocalFilesystemArtifactStoreAdapter


@dataclass
class LiteTask:
    stage: str
    job_id: str


class LitePipeline:
    """Scoped prepare->transcribe->merge path for M56 lite mode."""

    def __init__(self, artifacts: LocalFilesystemArtifactStoreAdapter):
        self._queue = InMemoryQueue()
        self._artifacts = artifacts

    async def run_job(self, audio_bytes: bytes, job_id: str | None = None) -> dict:
        job_id = job_id or str(uuid4())
        await self._artifacts.write_bytes(
            f"jobs/{job_id}/audio/original.wav", audio_bytes
        )
        await self._queue.enqueue(
            stage="prepare", task_id=str(uuid4()), job_id=job_id, timeout_s=30
        )

        while True:
            for stage in ("prepare", "transcribe", "merge"):
                envelope = await self._queue.consume(
                    stage=stage, consumer="lite", block_ms=10
                )
                if envelope is None:
                    continue
                if stage == "prepare":
                    await self._queue.enqueue(
                        stage="transcribe",
                        task_id=str(uuid4()),
                        job_id=envelope.job_id,
                        timeout_s=30,
                    )
                elif stage == "transcribe":
                    payload = {
                        "text": "lite transcript",
                        "segments": [{"text": "lite transcript"}],
                    }
                    await self._artifacts.write_bytes(
                        f"jobs/{envelope.job_id}/tasks/transcribe/output.json",
                        json.dumps(payload).encode("utf-8"),
                        content_type="application/json",
                    )
                    await self._queue.enqueue(
                        stage="merge",
                        task_id=str(uuid4()),
                        job_id=envelope.job_id,
                        timeout_s=30,
                    )
                else:
                    output_uri = await self._artifacts.write_bytes(
                        f"jobs/{envelope.job_id}/transcript.json",
                        json.dumps(
                            {
                                "job_id": envelope.job_id,
                                "status": "completed",
                                "text": "lite transcript",
                            }
                        ).encode("utf-8"),
                        content_type="application/json",
                    )
                    await self._queue.ack(stage=stage, message_id=envelope.message_id)
                    return {"job_id": envelope.job_id, "transcript_uri": output_uri}
                await self._queue.ack(stage=stage, message_id=envelope.message_id)

            await asyncio.sleep(0.01)


async def orchestrator_loop() -> None:
    settings = get_settings()
    if settings.runtime_mode != "lite":
        raise RuntimeError("lite_main can only run in DALSTON_MODE=lite")
    await init_db()


def main() -> None:
    asyncio.run(orchestrator_loop())


def build_default_pipeline() -> LitePipeline:
    settings = get_settings()
    if settings.runtime_mode != "lite":
        raise RuntimeError("Lite pipeline is only available in DALSTON_MODE=lite")
    return LitePipeline(
        LocalFilesystemArtifactStoreAdapter(settings.lite_artifacts_dir)
    )
