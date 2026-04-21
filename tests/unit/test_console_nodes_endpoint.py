"""Unit tests for the /api/console/nodes endpoint (M78)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from dalston.gateway.api.console import (
    NodesResponse,
    _parse_gb,
    get_nodes,
)

# ---------------------------------------------------------------------------
# _parse_gb helper
# ---------------------------------------------------------------------------


class TestParseGb:
    """Tests for the GPU memory string parser."""

    def test_standard_format(self):
        assert _parse_gb("4.2GB") == pytest.approx(4.2)

    def test_zero(self):
        assert _parse_gb("0GB") == 0.0

    def test_with_space(self):
        assert _parse_gb("16.0 GB") == pytest.approx(16.0)

    def test_integer(self):
        assert _parse_gb("8GB") == pytest.approx(8.0)

    def test_empty_string(self):
        assert _parse_gb("") == 0.0

    def test_no_unit(self):
        assert _parse_gb("4.2") == 0.0

    def test_invalid(self):
        assert _parse_gb("not-a-number") == 0.0

    def test_case_insensitive(self):
        assert _parse_gb("4.2gb") == pytest.approx(4.2)
        assert _parse_gb("4.2Gb") == pytest.approx(4.2)


# ---------------------------------------------------------------------------
# get_nodes endpoint
# ---------------------------------------------------------------------------


def _make_engine_hash(
    instance: str,
    engine_id: str,
    stage: str = "transcribe",
    node_id: str = "test-host",
    hostname: str = "test-host",
    deploy_env: str = "local",
    status: str = "idle",
    gpu_memory_used: str = "0GB",
    gpu_memory_total: str = "0GB",
    capacity: str = "1",
    aws_az: str = "",
    aws_instance_type: str = "",
) -> dict[str, str]:
    """Build a Redis hash dict mimicking an EngineRecord."""
    now = datetime.now(UTC).isoformat()
    return {
        "instance": instance,
        "engine_id": engine_id,
        "stage": stage,
        "status": status,
        "interfaces": json.dumps(["batch", "realtime"]),
        "capacity": capacity,
        "active_batch": "0",
        "active_realtime": "0",
        "gpu_memory_used": gpu_memory_used,
        "gpu_memory_total": gpu_memory_total,
        "last_heartbeat": now,
        "node_id": node_id,
        "hostname": hostname,
        "deploy_env": deploy_env,
        "aws_az": aws_az,
        "aws_instance_type": aws_instance_type,
    }


def _mock_principal():
    """Create a mock principal."""
    return MagicMock()


def _mock_security_manager():
    sm = MagicMock()
    sm.require_permission = MagicMock()
    return sm


@pytest.mark.asyncio
class TestGetNodes:
    """Tests for the get_nodes endpoint handler."""

    async def _call_endpoint(self, redis: AsyncMock) -> NodesResponse:
        """Call get_nodes with mocked dependencies."""
        principal = _mock_principal()
        with patch(
            "dalston.gateway.api.console.get_security_manager",
            return_value=_mock_security_manager(),
        ):
            return await get_nodes(principal=principal, redis=redis)

    async def test_empty_returns_no_nodes(self):
        """Empty instance set returns empty nodes list."""
        redis = AsyncMock()
        redis.smembers = AsyncMock(return_value=set())

        result = await self._call_endpoint(redis)

        assert result.nodes == []

    async def test_single_node_single_engine(self):
        """One engine instance produces one node with one engine."""
        redis = AsyncMock()
        redis.smembers = AsyncMock(return_value={"fw-abc123"})
        redis.hgetall = AsyncMock(
            return_value=_make_engine_hash(
                instance="fw-abc123",
                engine_id="faster-whisper",
                node_id="my-host",
                hostname="my-host",
            )
        )

        result = await self._call_endpoint(redis)

        assert len(result.nodes) == 1
        node = result.nodes[0]
        assert node.node_id == "my-host"
        assert node.hostname == "my-host"
        assert node.deploy_env == "local"
        assert node.engine_count == 1
        assert node.engines[0].engine_id == "faster-whisper"

    async def test_engines_grouped_by_node_id(self):
        """Multiple engines on the same node_id are grouped together."""
        redis = AsyncMock()
        redis.smembers = AsyncMock(return_value={"fw-1", "pa-2"})

        async def hgetall(key: str):
            if key.endswith("fw-1"):
                return _make_engine_hash(
                    instance="fw-1",
                    engine_id="faster-whisper",
                    stage="transcribe",
                    node_id="gpu-node-1",
                    hostname="gpu-node-1",
                    gpu_memory_used="4.2GB",
                    gpu_memory_total="16.0GB",
                )
            return _make_engine_hash(
                instance="pa-2",
                engine_id="pyannote",
                stage="diarize",
                node_id="gpu-node-1",
                hostname="gpu-node-1",
                gpu_memory_used="2.1GB",
                gpu_memory_total="16.0GB",
            )

        redis.hgetall = hgetall

        result = await self._call_endpoint(redis)

        assert len(result.nodes) == 1
        node = result.nodes[0]
        assert node.node_id == "gpu-node-1"
        assert node.engine_count == 2
        engine_ids = {e.engine_id for e in node.engines}
        assert engine_ids == {"faster-whisper", "pyannote"}

    async def test_gpu_aggregation(self):
        """GPU used and total are max across engines on a node — runners share
        one physical GPU, and nvidia-smi memory.used is a node-wide reading, so
        summing would double-count."""
        redis = AsyncMock()
        redis.smembers = AsyncMock(return_value={"fw-1", "pa-2"})

        async def hgetall(key: str):
            if key.endswith("fw-1"):
                return _make_engine_hash(
                    instance="fw-1",
                    engine_id="faster-whisper",
                    node_id="gpu-node",
                    hostname="gpu-node",
                    gpu_memory_used="4.2GB",
                    gpu_memory_total="16.0GB",
                )
            return _make_engine_hash(
                instance="pa-2",
                engine_id="pyannote",
                node_id="gpu-node",
                hostname="gpu-node",
                gpu_memory_used="2.1GB",
                gpu_memory_total="16.0GB",
            )

        redis.hgetall = hgetall

        result = await self._call_endpoint(redis)

        node = result.nodes[0]
        assert node.gpu_memory_used_gb == pytest.approx(4.2, abs=0.01)
        assert node.gpu_memory_total_gb == pytest.approx(16.0)

    async def test_sorting_aws_before_local(self):
        """AWS nodes sort before local nodes."""
        redis = AsyncMock()
        redis.smembers = AsyncMock(return_value={"local-1", "aws-1"})

        async def hgetall(key: str):
            if key.endswith("local-1"):
                return _make_engine_hash(
                    instance="local-1",
                    engine_id="audio-prepare",
                    node_id="my-mac",
                    hostname="my-mac",
                    deploy_env="local",
                )
            return _make_engine_hash(
                instance="aws-1",
                engine_id="faster-whisper",
                node_id="i-0abc123",
                hostname="ip-10-0-1-5",
                deploy_env="aws",
                aws_az="eu-west-2a",
                aws_instance_type="g4dn.xlarge",
            )

        redis.hgetall = hgetall

        result = await self._call_endpoint(redis)

        assert len(result.nodes) == 2
        assert result.nodes[0].deploy_env == "aws"
        assert result.nodes[0].node_id == "i-0abc123"
        assert result.nodes[1].deploy_env == "local"

    async def test_skips_engines_without_node_id(self):
        """Pre-M78 engines without node_id are skipped."""
        redis = AsyncMock()
        redis.smembers = AsyncMock(return_value={"old-1", "new-1"})

        async def hgetall(key: str):
            if key.endswith("old-1"):
                # Pre-M78: no node_id field
                return {
                    "instance": "old-1",
                    "engine_id": "legacy-engine",
                    "stage": "transcribe",
                    "status": "idle",
                    "last_heartbeat": datetime.now(UTC).isoformat(),
                }
            return _make_engine_hash(
                instance="new-1",
                engine_id="faster-whisper",
                node_id="my-host",
                hostname="my-host",
            )

        redis.hgetall = hgetall

        result = await self._call_endpoint(redis)

        assert len(result.nodes) == 1
        assert result.nodes[0].engines[0].engine_id == "faster-whisper"

    async def test_skips_empty_hashes(self):
        """Expired or empty Redis hashes are skipped."""
        redis = AsyncMock()
        redis.smembers = AsyncMock(return_value={"ghost-1"})
        redis.hgetall = AsyncMock(return_value={})

        result = await self._call_endpoint(redis)

        assert len(result.nodes) == 0

    async def test_engine_health_from_heartbeat(self):
        """Engine is_healthy is determined by heartbeat freshness."""
        redis = AsyncMock()
        redis.smembers = AsyncMock(return_value={"fw-1"})

        # Make a stale heartbeat (60 seconds old)
        from datetime import timedelta

        stale_time = (datetime.now(UTC) - timedelta(seconds=60)).isoformat()
        engine_data = _make_engine_hash(
            instance="fw-1",
            engine_id="faster-whisper",
            node_id="my-host",
            hostname="my-host",
        )
        engine_data["last_heartbeat"] = stale_time
        redis.hgetall = AsyncMock(return_value=engine_data)

        result = await self._call_endpoint(redis)

        assert len(result.nodes) == 1
        assert result.nodes[0].engines[0].is_healthy is False

    async def test_multiple_nodes(self):
        """Engines on different nodes produce separate NodeView entries."""
        redis = AsyncMock()
        redis.smembers = AsyncMock(return_value={"e1", "e2"})

        async def hgetall(key: str):
            if key.endswith("e1"):
                return _make_engine_hash(
                    instance="e1",
                    engine_id="faster-whisper",
                    node_id="node-a",
                    hostname="node-a",
                )
            return _make_engine_hash(
                instance="e2",
                engine_id="pyannote",
                node_id="node-b",
                hostname="node-b",
            )

        redis.hgetall = hgetall

        result = await self._call_endpoint(redis)

        assert len(result.nodes) == 2
        node_ids = {n.node_id for n in result.nodes}
        assert node_ids == {"node-a", "node-b"}

    async def test_cpu_only_node_gpu_zero(self):
        """CPU-only nodes have gpu_memory_total_gb = 0."""
        redis = AsyncMock()
        redis.smembers = AsyncMock(return_value={"e1"})
        redis.hgetall = AsyncMock(
            return_value=_make_engine_hash(
                instance="e1",
                engine_id="audio-prepare",
                stage="prepare",
                node_id="cpu-host",
                hostname="cpu-host",
                gpu_memory_total="0GB",
                gpu_memory_used="0GB",
            )
        )

        result = await self._call_endpoint(redis)

        assert result.nodes[0].gpu_memory_total_gb == 0.0
        assert result.nodes[0].gpu_memory_used_gb == 0.0
