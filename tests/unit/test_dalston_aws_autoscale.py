"""Tests for M91 autoscaler glue in infra/scripts/dalston-aws.

Loads the extensionless script via SourceFileLoader (same pattern as
test_dalston_aws_presets.py) and exercises the pure helpers: the
provisioning script generator (dry-run propagation) and fleet tag builder
(atomic RunInstances tagging).
"""

from __future__ import annotations

import importlib.util
import sys
from importlib.machinery import SourceFileLoader
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "infra" / "scripts" / "dalston-aws"


def _load_dalston_aws():
    loader = SourceFileLoader("dalston_aws_autoscale_under_test", str(SCRIPT_PATH))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec and spec.loader, f"could not load {SCRIPT_PATH}"
    module = importlib.util.module_from_spec(spec)
    sys.modules[loader.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def daws():
    return _load_dalston_aws()


CTX = {
    "region": "eu-west-2",
    "gpu_sg_id": "sg-123",
    "key_name": "dalston-key",
    "instance_profile": "dalston-instance-profile",
    "s3_bucket": "bucket",
    "registry": "ghcr.io/x",
    "observability": False,
    "redis_url": "redis://localhost:6379",
}


class TestProvisionScript:
    def test_live_mode_omits_dry_run_flag(self, daws) -> None:
        script = daws._autoscale_provision_script(CTX)
        assert "autoscale --once\n" in script or "autoscale --once\nASSVCEOF" in script
        assert "--once --dry-run" not in script
        assert "LIVE actuation" in script

    def test_dry_run_mode_installs_dry_run_tick(self, daws) -> None:
        """--provision --dry-run must NOT silently enable live actuation."""
        script = daws._autoscale_provision_script(CTX, dry_run=True)
        assert "autoscale --once --dry-run" in script
        assert "DRY-RUN" in script

    def test_context_yaml_embedded(self, daws) -> None:
        script = daws._autoscale_provision_script(CTX)
        assert "gpu_sg_id: sg-123" in script
        assert "autoscale-context.yaml" in script

    def test_policy_seeded_only_if_absent(self, daws) -> None:
        script = daws._autoscale_provision_script(CTX)
        assert "if [ ! -f /data/dalston/autoscale-policy.yaml ]" in script


class TestFleetTags:
    def test_fleet_tags_shape_is_order_independent(self, daws) -> None:
        tags_a = daws.fleet_tags(["pyannote", "nemo"], "autoscaler")
        tags_b = daws.fleet_tags(["nemo", "pyannote"], "autoscaler")
        assert tags_a == tags_b
        by_key = {t["Key"]: t["Value"] for t in tags_a}
        assert by_key["dalston:role"] == "gpu-worker"
        assert by_key["dalston:shape"] == "nemo+pyannote"
        assert by_key["dalston:managed-by"] == "autoscaler"
