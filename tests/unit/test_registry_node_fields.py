"""Unit tests for EngineRecord node identity fields (M78).

Tests serialization round-trip for the new hostname, node_id, deploy_env,
aws_az, and aws_instance_type fields added in M78.
"""

from __future__ import annotations

from datetime import UTC, datetime

from dalston.common.registry import (
    EngineRecord,
    _mapping_to_record,
    _record_to_mapping,
)


def _make_record(**overrides) -> EngineRecord:
    defaults = {
        "instance": "faster-whisper-abc123",
        "engine_id": "faster-whisper",
        "stage": "transcribe",
        "status": "idle",
        "interfaces": ["batch"],
        "last_heartbeat": datetime.now(UTC),
    }
    defaults.update(overrides)
    return EngineRecord(**defaults)


class TestNodeFieldsSerialization:
    """Tests for serialization round-trip of M78 node fields."""

    def test_local_node_round_trip(self):
        """Local node fields survive serialization and deserialization."""
        record = _make_record(
            hostname="my-macbook",
            node_id="my-macbook",
            deploy_env="local",
            aws_az=None,
            aws_instance_type=None,
        )
        mapping = _record_to_mapping(record)

        assert mapping["hostname"] == "my-macbook"
        assert mapping["node_id"] == "my-macbook"
        assert mapping["deploy_env"] == "local"
        assert "aws_az" not in mapping  # None fields are omitted
        assert "aws_instance_type" not in mapping

        restored = _mapping_to_record(record.instance, mapping)
        assert restored is not None
        assert restored.hostname == "my-macbook"
        assert restored.node_id == "my-macbook"
        assert restored.deploy_env == "local"
        assert restored.aws_az is None
        assert restored.aws_instance_type is None

    def test_aws_node_round_trip(self):
        """AWS node fields survive serialization and deserialization."""
        record = _make_record(
            hostname="ip-10-0-1-5",
            node_id="i-0abc123def456",
            deploy_env="aws",
            aws_az="eu-west-2a",
            aws_instance_type="g4dn.xlarge",
        )
        mapping = _record_to_mapping(record)

        assert mapping["hostname"] == "ip-10-0-1-5"
        assert mapping["node_id"] == "i-0abc123def456"
        assert mapping["deploy_env"] == "aws"
        assert mapping["aws_az"] == "eu-west-2a"
        assert mapping["aws_instance_type"] == "g4dn.xlarge"

        restored = _mapping_to_record(record.instance, mapping)
        assert restored is not None
        assert restored.hostname == "ip-10-0-1-5"
        assert restored.node_id == "i-0abc123def456"
        assert restored.deploy_env == "aws"
        assert restored.aws_az == "eu-west-2a"
        assert restored.aws_instance_type == "g4dn.xlarge"

    def test_defaults_when_fields_missing(self):
        """Missing node fields in Redis hash produce sensible defaults."""
        # Simulate a minimal Redis hash with no M78 fields
        mapping = {
            "instance": "fw-abc123",
            "engine_id": "faster-whisper",
            "stage": "transcribe",
            "status": "idle",
            "interfaces": '["batch"]',
            "capacity": "1",
            "active_batch": "0",
            "active_realtime": "0",
            "gpu_memory_used": "0GB",
            "gpu_memory_total": "0GB",
            "last_heartbeat": datetime.now(UTC).isoformat(),
            "registered_at": datetime.now(UTC).isoformat(),
            "supports_word_timestamps": "false",
            "includes_diarization": "false",
            "execution_profile": "container",
        }
        restored = _mapping_to_record("fw-abc123", mapping)

        assert restored is not None
        assert restored.hostname == ""
        assert restored.node_id == ""
        assert restored.deploy_env == "local"
        assert restored.aws_az is None
        assert restored.aws_instance_type is None

    def test_node_fields_do_not_break_existing_records(self):
        """Records without M78 fields still deserialize correctly."""
        record = _make_record()  # default empty node fields
        mapping = _record_to_mapping(record)
        restored = _mapping_to_record(record.instance, mapping)

        assert restored is not None
        assert restored.engine_id == "faster-whisper"
        assert restored.stage == "transcribe"
        # Node fields have their dataclass defaults
        assert restored.hostname == ""
        assert restored.node_id == ""
        assert restored.deploy_env == "local"
