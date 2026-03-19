"""Unit tests for node identity detection (M78)."""

from __future__ import annotations

import socket
from unittest.mock import MagicMock, patch

import pytest

from dalston.common.node_identity import (
    NodeIdentity,
    _probe_imds,
    detect_node_identity,
)


@pytest.fixture(autouse=True)
def _clear_identity_cache():
    """Clear the lru_cache between tests."""
    detect_node_identity.cache_clear()
    yield
    detect_node_identity.cache_clear()


# ---------------------------------------------------------------------------
# _probe_imds
# ---------------------------------------------------------------------------


def _mock_urlopen(responses):
    """Create a mock urlopen that returns different responses per call."""
    call_count = 0

    def side_effect(req, **kwargs):
        nonlocal call_count
        resp = responses[call_count]
        call_count += 1
        if isinstance(resp, Exception):
            raise resp
        mock_resp = MagicMock()
        mock_resp.read.return_value = resp.encode() if isinstance(resp, str) else resp
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        return mock_resp

    return side_effect


class TestProbeImds:
    """Tests for the low-level IMDSv2 probe."""

    def test_returns_dict_on_success(self):
        """Successful IMDS probe returns parsed identity document."""
        import json

        identity_doc = {
            "instanceId": "i-0abc123def456",
            "instanceType": "g4dn.xlarge",
            "availabilityZone": "eu-west-2a",
            "region": "eu-west-2",
        }
        mock = _mock_urlopen(["fake-token", json.dumps(identity_doc)])

        with patch(
            "dalston.common.node_identity.urllib.request.urlopen", side_effect=mock
        ):
            result = _probe_imds()

        assert result is not None
        assert result["instanceId"] == "i-0abc123def456"
        assert result["instanceType"] == "g4dn.xlarge"

    def test_returns_none_on_timeout(self):
        """IMDS probe returns None when connection times out (non-EC2 host)."""
        from urllib.error import URLError

        mock = _mock_urlopen([URLError("timed out")])

        with patch(
            "dalston.common.node_identity.urllib.request.urlopen", side_effect=mock
        ):
            result = _probe_imds()

        assert result is None

    def test_returns_none_on_connection_refused(self):
        """IMDS probe returns None when connection is refused."""
        from urllib.error import URLError

        mock = _mock_urlopen([URLError("connection refused")])

        with patch(
            "dalston.common.node_identity.urllib.request.urlopen", side_effect=mock
        ):
            result = _probe_imds()

        assert result is None


# ---------------------------------------------------------------------------
# detect_node_identity
# ---------------------------------------------------------------------------


class TestDetectNodeIdentity:
    """Tests for the cached identity detection function."""

    def test_env_var_local_skips_imds(self, monkeypatch):
        """When DALSTON_DEPLOY_ENV=local, IMDS is never probed."""
        monkeypatch.setenv("DALSTON_DEPLOY_ENV", "local")
        with patch("dalston.common.node_identity._probe_imds") as mock_probe:
            identity = detect_node_identity()

        mock_probe.assert_not_called()
        assert identity.deploy_env == "local"
        assert identity.hostname == socket.gethostname()
        assert identity.node_id == socket.gethostname()
        assert identity.region is None
        assert identity.instance_type is None

    def test_env_var_docker_skips_imds(self, monkeypatch):
        """When DALSTON_DEPLOY_ENV=docker, IMDS is never probed."""
        monkeypatch.setenv("DALSTON_DEPLOY_ENV", "docker")
        with patch("dalston.common.node_identity._probe_imds") as mock_probe:
            identity = detect_node_identity()

        mock_probe.assert_not_called()
        assert identity.deploy_env == "docker"

    def test_aws_from_imds(self):
        """When IMDS responds, identity is populated from EC2 metadata."""
        identity_doc = {
            "instanceId": "i-0abc123def456",
            "instanceType": "g4dn.xlarge",
            "availabilityZone": "eu-west-2a",
        }
        with patch(
            "dalston.common.node_identity._probe_imds", return_value=identity_doc
        ):
            identity = detect_node_identity()

        assert identity.deploy_env == "aws"
        assert identity.node_id == "i-0abc123def456"
        assert identity.instance_type == "g4dn.xlarge"
        assert identity.region == "eu-west-2a"
        assert identity.hostname == socket.gethostname()

    def test_fallback_to_local(self):
        """When IMDS fails and no env var, fallback to local."""
        with patch("dalston.common.node_identity._probe_imds", return_value=None):
            identity = detect_node_identity()

        assert identity.deploy_env == "local"
        assert identity.node_id == socket.gethostname()
        assert identity.region is None
        assert identity.instance_type is None

    def test_env_var_aws_forces_probe(self, monkeypatch):
        """When DALSTON_DEPLOY_ENV=aws, IMDS is probed even on non-EC2."""
        monkeypatch.setenv("DALSTON_DEPLOY_ENV", "aws")
        with patch(
            "dalston.common.node_identity._probe_imds", return_value=None
        ) as mock_probe:
            identity = detect_node_identity()

        mock_probe.assert_called_once()
        # Probe failed, so fallback
        assert identity.deploy_env == "local"

    def test_caching(self):
        """detect_node_identity returns cached result on second call."""
        with patch("dalston.common.node_identity._probe_imds", return_value=None):
            first = detect_node_identity()

        # Second call should not probe again (cache hit)
        with patch("dalston.common.node_identity._probe_imds") as mock_probe:
            second = detect_node_identity()

        mock_probe.assert_not_called()
        assert first is second

    def test_host_hostname_used_as_node_id(self, monkeypatch):
        """DALSTON_HOST_HOSTNAME overrides node_id for Docker grouping."""
        monkeypatch.setenv("DALSTON_HOST_HOSTNAME", "my-macbook.local")
        with patch("dalston.common.node_identity._probe_imds", return_value=None):
            identity = detect_node_identity()

        assert identity.node_id == "my-macbook.local"
        # hostname is still the container's own hostname
        assert identity.hostname == socket.gethostname()
        assert identity.deploy_env == "local"

    def test_host_hostname_with_deploy_env(self, monkeypatch):
        """DALSTON_HOST_HOSTNAME works together with DALSTON_DEPLOY_ENV."""
        monkeypatch.setenv("DALSTON_HOST_HOSTNAME", "my-macbook.local")
        monkeypatch.setenv("DALSTON_DEPLOY_ENV", "local")
        with patch("dalston.common.node_identity._probe_imds") as mock_probe:
            identity = detect_node_identity()

        mock_probe.assert_not_called()
        assert identity.node_id == "my-macbook.local"
        assert identity.hostname == socket.gethostname()

    def test_aws_imds_ignores_host_hostname(self, monkeypatch):
        """On EC2, IMDS instance ID takes priority over DALSTON_HOST_HOSTNAME."""
        monkeypatch.setenv("DALSTON_HOST_HOSTNAME", "should-be-ignored")
        identity_doc = {
            "instanceId": "i-0abc123def456",
            "instanceType": "g4dn.xlarge",
            "availabilityZone": "eu-west-2a",
        }
        with patch(
            "dalston.common.node_identity._probe_imds", return_value=identity_doc
        ):
            identity = detect_node_identity()

        assert identity.node_id == "i-0abc123def456"

    def test_frozen_dataclass(self):
        """NodeIdentity is immutable."""
        identity = NodeIdentity(
            hostname="test",
            node_id="test",
            deploy_env="local",
            region=None,
            instance_type=None,
        )
        with pytest.raises(AttributeError):
            identity.hostname = "changed"  # type: ignore[misc]
