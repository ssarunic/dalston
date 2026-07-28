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


# ---------------------------------------------------------------------------
# Mocked side-effect tests: the AWS mutation paths dry-run never exercises
# ---------------------------------------------------------------------------

from contextlib import contextmanager  # noqa: E402
from datetime import UTC, datetime, timedelta  # noqa: E402
from unittest.mock import MagicMock  # noqa: E402

from botocore.exceptions import ClientError  # noqa: E402


def _policy(daws, **overrides):
    base = {
        "engines": ["nemo", "pyannote"],
        "stream_engine_ids": ["nemo", "pyannote-4.0"],
    }
    base.update(overrides)
    import dalston_autoscale

    return dalston_autoscale.ShapePolicy.from_dict(base)


def _ctx(daws):
    return daws.AutoscaleContext(
        region="eu-west-2",
        gpu_sg_id="sg-123",
        key_name="dalston-key",
        instance_profile="dalston-instance-profile",
        s3_bucket="bucket",
    )


class TestApplyDecisionRouting:
    def test_launch_calls_autoscale_launch_under_lock(self, daws, monkeypatch) -> None:
        import dalston_autoscale as das

        entered = []

        @contextmanager
        def fake_lock():
            entered.append(True)
            yield

        monkeypatch.setattr(daws, "_launch_lock", fake_lock)
        launch = MagicMock(return_value="launched i-new (g4dn.xlarge)")
        monkeypatch.setattr(daws, "_autoscale_launch", launch)

        result = daws._apply_decision(
            MagicMock(),
            _ctx(daws),
            _policy(daws),
            das.ScaleAction.LAUNCH,
            das.FleetSnapshot(),
            {},
        )

        assert entered and launch.called
        assert result == "launched i-new (g4dn.xlarge)"

    def test_spot_capacity_error_audits_and_skips(self, daws, monkeypatch) -> None:
        import dalston_autoscale as das

        @contextmanager
        def fake_lock():
            yield

        monkeypatch.setattr(daws, "_launch_lock", fake_lock)
        monkeypatch.setattr(
            daws,
            "_autoscale_launch",
            MagicMock(side_effect=daws.SpotCapacityError("no capacity")),
        )
        audit = MagicMock()
        monkeypatch.setattr(daws, "audit", audit)

        result = daws._apply_decision(
            MagicMock(),
            _ctx(daws),
            _policy(daws),
            das.ScaleAction.LAUNCH,
            das.FleetSnapshot(),
            {},
        )

        assert "no spot capacity" in result
        assert audit.call_args[0][0] == "autoscale.spot_capacity_error"

    def test_lock_contention_skips_tick_gracefully(self, daws, monkeypatch) -> None:
        import dalston_autoscale as das

        @contextmanager
        def held_lock():
            raise daws.LaunchLockHeldError("held")
            yield  # pragma: no cover

        monkeypatch.setattr(daws, "_launch_lock", held_lock)

        result = daws._apply_decision(
            MagicMock(),
            _ctx(daws),
            _policy(daws),
            das.ScaleAction.LAUNCH,
            das.FleetSnapshot(),
            {},
        )

        assert "launch lock held" in result

    def test_terminate_only_picks_autoscaler_managed(self, daws, monkeypatch) -> None:
        import dalston_autoscale as das

        term = MagicMock(return_value="terminated i-auto2")
        monkeypatch.setattr(daws, "_autoscale_terminate", term)

        fleet = das.FleetSnapshot(live_instance_ids=("i-manual", "i-auto1", "i-auto2"))
        managed = {
            "i-manual": "manual",
            "i-auto1": "autoscaler",
            "i-auto2": "autoscaler",
        }
        result = daws._apply_decision(
            MagicMock(),
            _ctx(daws),
            _policy(daws),
            das.ScaleAction.TERMINATE,
            fleet,
            managed,
        )

        # newest autoscaler-managed instance, never the manual one
        assert term.call_args[0][3] == "i-auto2"
        assert result == "terminated i-auto2"

    def test_terminate_with_manual_only_fleet_is_noop(self, daws, monkeypatch) -> None:
        import dalston_autoscale as das

        term = MagicMock()
        monkeypatch.setattr(daws, "_autoscale_terminate", term)

        result = daws._apply_decision(
            MagicMock(),
            _ctx(daws),
            _policy(daws),
            das.ScaleAction.TERMINATE,
            das.FleetSnapshot(live_instance_ids=("i-manual",)),
            {"i-manual": "manual"},
        )

        assert not term.called
        assert "no autoscaler-managed instance" in result


class TestReapStuckPending:
    def _fleet(self, daws, pending):
        import dalston_autoscale as das

        return das.FleetSnapshot(pending_instance_ids=tuple(pending))

    def test_reaps_only_old_autoscaler_managed(self, daws, monkeypatch) -> None:
        now = datetime.now(UTC)
        term = MagicMock()
        publish = MagicMock()
        audit = MagicMock()
        monkeypatch.setattr(daws, "terminate_gpu_worker", term)
        monkeypatch.setattr(daws, "_publish_autoscale_event", publish)
        monkeypatch.setattr(daws, "audit", audit)

        stuck, reaped = daws._reap_stuck_pending(
            MagicMock(),
            _ctx(daws),
            _policy(daws),
            self._fleet(daws, ["i-old-auto", "i-new-auto", "i-old-manual"]),
            {
                "i-old-auto": "autoscaler",
                "i-new-auto": "autoscaler",
                "i-old-manual": "manual",
            },
            {
                "i-old-auto": now - timedelta(seconds=3600),
                "i-new-auto": now - timedelta(seconds=60),
                "i-old-manual": now - timedelta(seconds=7200),
            },
            now,
            dry_run=False,
        )

        assert stuck == reaped == ["i-old-auto"]
        term.assert_called_once_with("eu-west-2", "i-old-auto")
        assert audit.call_args[0][0] == "autoscale.boot_timeout"
        assert publish.call_args[0][1] == "autoscaler.boot_timeout"

    def test_dry_run_reports_but_never_terminates(self, daws, monkeypatch) -> None:
        now = datetime.now(UTC)
        term = MagicMock()
        monkeypatch.setattr(daws, "terminate_gpu_worker", term)

        stuck, reaped = daws._reap_stuck_pending(
            MagicMock(),
            _ctx(daws),
            _policy(daws),
            self._fleet(daws, ["i-old-auto"]),
            {"i-old-auto": "autoscaler"},
            {"i-old-auto": now - timedelta(seconds=3600)},
            now,
            dry_run=True,
        )

        assert stuck == ["i-old-auto"] and reaped == []
        assert not term.called

    def test_terminate_failure_is_contained(self, daws, monkeypatch) -> None:
        now = datetime.now(UTC)
        err = ClientError(
            {"Error": {"Code": "X", "Message": "boom"}}, "TerminateInstances"
        )
        monkeypatch.setattr(daws, "terminate_gpu_worker", MagicMock(side_effect=err))
        monkeypatch.setattr(daws, "audit", MagicMock())
        monkeypatch.setattr(daws, "_publish_autoscale_event", MagicMock())

        stuck, reaped = daws._reap_stuck_pending(
            MagicMock(),
            _ctx(daws),
            _policy(daws),
            self._fleet(daws, ["i-old-auto"]),
            {"i-old-auto": "autoscaler"},
            {"i-old-auto": now - timedelta(seconds=3600)},
            now,
            dry_run=False,
        )

        assert stuck == ["i-old-auto"] and reaped == []


class TestAutoscaleTerminateDrain:
    def test_drains_then_terminates_and_publishes(self, daws, monkeypatch) -> None:
        import dalston_autoscale as das

        r = MagicMock()
        r.scan_iter.return_value = iter(
            ["dalston:engine:instance:a", "dalston:engine:instance:b"]
        )
        r.hget.side_effect = ["i-target", "i-other"]

        idle = das.EngineBacklog("nemo", 0, 0)
        monkeypatch.setattr(daws, "_fetch_engine_backlog", MagicMock(return_value=idle))
        term = MagicMock()
        publish = MagicMock()
        audit = MagicMock()
        monkeypatch.setattr(daws, "terminate_gpu_worker", term)
        monkeypatch.setattr(daws, "_publish_autoscale_event", publish)
        monkeypatch.setattr(daws, "audit", audit)

        result = daws._autoscale_terminate(r, _ctx(daws), _policy(daws), "i-target")

        # advisory draining set only on the target node's registry records
        r.hset.assert_called_once_with(
            "dalston:engine:instance:a", "status", "draining"
        )
        term.assert_called_once_with("eu-west-2", "i-target")
        assert publish.call_args[0][1] == "autoscaler.scale_down"
        assert result == "terminated i-target"

    def test_drain_timeout_defers_to_next_tick(self, daws, monkeypatch) -> None:
        import dalston_autoscale as das

        r = MagicMock()
        r.scan_iter.return_value = iter([])
        busy = das.EngineBacklog("nemo", 0, 2)  # in-flight work never drains
        monkeypatch.setattr(daws, "_fetch_engine_backlog", MagicMock(return_value=busy))
        term = MagicMock()
        audit = MagicMock()
        monkeypatch.setattr(daws, "terminate_gpu_worker", term)
        monkeypatch.setattr(daws, "audit", audit)

        policy = _policy(daws, drain_wait_s=0)  # immediate deadline, no sleep
        result = daws._autoscale_terminate(r, _ctx(daws), policy, "i-target")

        assert not term.called
        assert "drain timeout" in result
        assert audit.call_args[0][0] == "autoscale.drain_timeout"


class TestDownAndTerminateRouting:
    def _state(self, daws):
        return daws.DeploymentState(
            region="eu-west-2", scenario="split", instance_id="i-cp"
        )

    def test_down_terminates_autoscaler_and_stops_manual(
        self, daws, monkeypatch
    ) -> None:
        st = self._state(daws)
        monkeypatch.setattr(daws, "require_state", lambda: st)
        monkeypatch.setattr(daws, "_adopt_legacy_gpu_tags", MagicMock())
        monkeypatch.setattr(daws, "audit", MagicMock())
        monkeypatch.setattr(daws, "ec2_client", MagicMock())
        monkeypatch.setattr(
            daws,
            "discover_gpu_workers",
            MagicMock(
                return_value=[
                    {"instance_id": "i-auto", "managed_by": "autoscaler"},
                    {"instance_id": "i-manual", "managed_by": "manual"},
                ]
            ),
        )
        term = MagicMock()
        stop = MagicMock()
        monkeypatch.setattr(daws, "terminate_gpu_worker", term)
        monkeypatch.setattr(daws, "_stop_instance", stop)

        daws.cmd_down(MagicMock())

        term.assert_called_once_with("eu-west-2", "i-auto")
        stopped_ids = [c.args[2] for c in stop.call_args_list]
        assert stopped_ids == ["i-manual", "i-cp"]

    def test_terminate_control_plane_terminates_gpu_first(
        self, daws, monkeypatch
    ) -> None:
        st = self._state(daws)
        monkeypatch.setattr(daws, "require_state", lambda: st)
        calls = []
        monkeypatch.setattr(
            daws, "_terminate_gpu", lambda st, name=None: calls.append(("gpu", name))
        )
        monkeypatch.setattr(
            daws, "_terminate_control_plane", lambda st: calls.append(("cp", None))
        )
        monkeypatch.setattr(daws, "cmd_status_impl", MagicMock())

        args = MagicMock()
        args.target = "control-plane"
        args.name = None
        daws.cmd_terminate(args)

        assert calls == [("gpu", None), ("cp", None)]

    def test_terminate_gpu_target_passes_name_filter(self, daws, monkeypatch) -> None:
        st = self._state(daws)
        monkeypatch.setattr(daws, "require_state", lambda: st)
        calls = []
        monkeypatch.setattr(
            daws, "_terminate_gpu", lambda st, name=None: calls.append(("gpu", name))
        )
        monkeypatch.setattr(
            daws, "_terminate_control_plane", lambda st: calls.append(("cp", None))
        )
        monkeypatch.setattr(daws, "cmd_status_impl", MagicMock())

        args = MagicMock()
        args.target = "gpu"
        args.name = "i-0123"
        daws.cmd_terminate(args)

        assert calls == [("gpu", "i-0123")]
