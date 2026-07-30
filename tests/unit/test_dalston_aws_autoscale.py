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
            datetime.now(UTC),
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
            datetime.now(UTC),
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
            datetime.now(UTC),
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
            datetime.now(UTC),
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
            datetime.now(UTC),
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
            daws,
            "_terminate_control_plane",
            lambda st, delete_data=False: calls.append(("cp", delete_data)),
        )
        monkeypatch.setattr(daws, "cmd_status_impl", MagicMock())

        args = MagicMock()
        args.target = "control-plane"
        args.name = None
        args.delete_data = False
        daws.cmd_terminate(args)

        # /data volume is kept unless --delete-data is passed explicitly
        assert calls == [("gpu", None), ("cp", False)]

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


class TestPublishPendingNodes:
    def test_writes_pending_and_deletes_live(self, daws) -> None:
        import dalston_autoscale as das

        r = MagicMock()
        now = datetime.now(UTC)
        launch = now - timedelta(seconds=120)
        fleet = das.FleetSnapshot(
            live_instance_ids=("i-live",), pending_instance_ids=("i-boot",)
        )
        workers = {
            "i-boot": {
                "instance_id": "i-boot",
                "managed_by": "autoscaler",
                "instance_type": "g4dn.xlarge",
                "launch_time": launch,
            },
            "i-live": {"instance_id": "i-live", "managed_by": "autoscaler"},
        }

        daws._publish_pending_nodes(r, _policy(daws), fleet, workers, now)

        key = "dalston:autoscale:pending:i-boot"
        r.hset.assert_called_once()
        assert r.hset.call_args[0][0] == key
        mapping = r.hset.call_args[1]["mapping"]
        assert mapping["shape"] == "nemo+pyannote"
        assert mapping["gpu_type"] == "g4dn.xlarge"
        assert mapping["managed_by"] == "autoscaler"
        assert mapping["launch_time"] == launch.isoformat()
        r.expire.assert_called_once_with(key, daws.AUTOSCALE_PENDING_TTL_S)
        r.delete.assert_called_once_with("dalston:autoscale:pending:i-live")

    def test_no_pending_no_writes(self, daws) -> None:
        import dalston_autoscale as das

        r = MagicMock()
        daws._publish_pending_nodes(
            r, _policy(daws), das.FleetSnapshot(), {}, datetime.now(UTC)
        )
        assert not r.hset.called


# ---------------------------------------------------------------------------
# M95: shapes marker, blocked state, tick snapshot
# ---------------------------------------------------------------------------


class TestShapesMarker:
    def test_atomic_rewrite_from_policy(self, daws) -> None:
        r = MagicMock()
        pipe = MagicMock()
        r.pipeline.return_value = pipe
        now = datetime.now(UTC)

        daws._publish_shapes_marker(r, [_policy(daws)], now, dry_run=False)

        r.pipeline.assert_called_once_with(transaction=True)
        pipe.delete.assert_called_once_with("dalston:autoscale:shapes")
        pipe.hset.assert_called_once_with(
            "dalston:autoscale:shapes",
            mapping={"nemo+pyannote": now.isoformat()},
        )
        pipe.execute.assert_called_once()

    def test_empty_shapes_still_deletes_but_skips_hset(self, daws) -> None:
        r = MagicMock()
        pipe = MagicMock()
        r.pipeline.return_value = pipe

        daws._publish_shapes_marker(r, [], datetime.now(UTC), dry_run=False)

        pipe.delete.assert_called_once()
        assert not pipe.hset.called
        pipe.execute.assert_called_once()

    def test_dry_run_uses_shadow_key(self, daws) -> None:
        r = MagicMock()
        pipe = MagicMock()
        r.pipeline.return_value = pipe

        daws._publish_shapes_marker(r, [_policy(daws)], datetime.now(UTC), dry_run=True)

        pipe.delete.assert_called_once_with("dalston:autoscale:shapes:dryrun")


class TestBlockedState:
    """The counter must accumulate across ticks WITH backlog present —
    the reason this state has its own key instead of the idle-state hash
    (which is deleted whenever backlog exists)."""

    def test_first_failure_starts_streak(self, daws) -> None:
        r = MagicMock()
        r.hgetall.return_value = {}
        now = datetime.now(UTC)

        daws._write_blocked_state(
            r, "nemo+pyannote", "spot_quota", "quota hit", now, dry_run=False
        )

        key = "dalston:autoscale:blocked:nemo+pyannote"
        mapping = r.hset.call_args[1]["mapping"]
        assert r.hset.call_args[0][0] == key
        assert mapping["kind"] == "spot_quota"
        assert mapping["since"] == now.isoformat()
        assert mapping["ticks"] == "1"
        r.expire.assert_called_once_with(key, daws.AUTOSCALE_BLOCKED_TTL_S)

    def test_streak_accumulates_and_preserves_since(self, daws) -> None:
        r = MagicMock()
        first_since = "2026-07-30T11:58:00+00:00"
        r.hgetall.return_value = {
            "kind": "spot_quota",
            "since": first_since,
            "ticks": "5",
            "detail": "quota hit",
        }

        daws._write_blocked_state(
            r,
            "nemo+pyannote",
            "spot_quota",
            "quota hit again",
            datetime.now(UTC),
            dry_run=False,
        )

        mapping = r.hset.call_args[1]["mapping"]
        assert mapping["ticks"] == "6"
        assert mapping["since"] == first_since

    def test_read_round_trip(self, daws) -> None:
        r = MagicMock()
        r.hgetall.return_value = {
            "kind": "spot_capacity",
            "since": "2026-07-30T11:58:00+00:00",
            "ticks": "3",
            "detail": "no capacity",
        }

        info = daws._read_blocked_state(r, "nemo+pyannote", dry_run=False)

        assert info is not None
        assert info.kind == "spot_capacity"
        assert info.consecutive_ticks == 3

    def test_read_malformed_returns_none(self, daws) -> None:
        r = MagicMock()
        r.hgetall.return_value = {"kind": "not-a-kind", "ticks": "1", "since": "x"}
        assert daws._read_blocked_state(r, "s", dry_run=False) is None
        r.hgetall.return_value = {"kind": "spot_quota", "ticks": "NaN", "since": "x"}
        assert daws._read_blocked_state(r, "s", dry_run=False) is None

    def test_clear_deletes_key(self, daws) -> None:
        r = MagicMock()
        daws._clear_blocked_state(r, "nemo+pyannote", dry_run=False)
        r.delete.assert_called_once_with("dalston:autoscale:blocked:nemo+pyannote")

    def test_apply_decision_launch_success_clears_streak(
        self, daws, monkeypatch
    ) -> None:
        import dalston_autoscale as das

        @contextmanager
        def fake_lock():
            yield

        monkeypatch.setattr(daws, "_launch_lock", fake_lock)
        monkeypatch.setattr(daws, "_autoscale_launch", MagicMock(return_value="ok"))
        clear = MagicMock()
        monkeypatch.setattr(daws, "_clear_blocked_state", clear)

        daws._apply_decision(
            MagicMock(),
            _ctx(daws),
            _policy(daws),
            das.ScaleAction.LAUNCH,
            das.FleetSnapshot(),
            {},
            datetime.now(UTC),
        )
        assert clear.called

    def test_apply_decision_quota_error_writes_streak(self, daws, monkeypatch) -> None:
        import dalston_autoscale as das

        @contextmanager
        def fake_lock():
            yield

        monkeypatch.setattr(daws, "_launch_lock", fake_lock)
        monkeypatch.setattr(
            daws,
            "_autoscale_launch",
            MagicMock(side_effect=daws.SpotQuotaError("quota")),
        )
        monkeypatch.setattr(daws, "audit", MagicMock())
        write = MagicMock()
        monkeypatch.setattr(daws, "_write_blocked_state", write)

        daws._apply_decision(
            MagicMock(),
            _ctx(daws),
            _policy(daws),
            das.ScaleAction.LAUNCH,
            das.FleetSnapshot(),
            {},
            datetime.now(UTC),
        )
        assert write.call_args[0][2] == "spot_quota"

    def test_apply_decision_lock_held_leaves_streak(self, daws, monkeypatch) -> None:
        import dalston_autoscale as das

        @contextmanager
        def held_lock():
            raise daws.LaunchLockHeldError("held")
            yield  # pragma: no cover

        monkeypatch.setattr(daws, "_launch_lock", held_lock)
        write = MagicMock()
        clear = MagicMock()
        monkeypatch.setattr(daws, "_write_blocked_state", write)
        monkeypatch.setattr(daws, "_clear_blocked_state", clear)

        daws._apply_decision(
            MagicMock(),
            _ctx(daws),
            _policy(daws),
            das.ScaleAction.LAUNCH,
            das.FleetSnapshot(),
            {},
            datetime.now(UTC),
        )
        assert not write.called
        assert not clear.called

    def test_apply_decision_no_launch_needed_clears_streak(
        self, daws, monkeypatch
    ) -> None:
        import dalston_autoscale as das

        clear = MagicMock()
        monkeypatch.setattr(daws, "_clear_blocked_state", clear)

        daws._apply_decision(
            MagicMock(),
            _ctx(daws),
            _policy(daws),
            das.ScaleAction.NONE,
            das.FleetSnapshot(),
            {},
            datetime.now(UTC),
        )
        assert clear.called


class TestTickSnapshotWrite:
    def test_writes_json_with_ttl(self, daws) -> None:
        import json as _json

        r = MagicMock()
        daws._write_tick_snapshot(
            r, "nemo+pyannote", {"schema_version": 1}, dry_run=False
        )
        args, kwargs = r.set.call_args
        assert args[0] == "dalston:autoscale:tick:nemo+pyannote"
        assert _json.loads(args[1]) == {"schema_version": 1}
        assert kwargs["ex"] == daws.AUTOSCALE_TICK_TTL_S

    def test_dry_run_uses_shadow_key(self, daws) -> None:
        r = MagicMock()
        daws._write_tick_snapshot(r, "nemo+pyannote", {}, dry_run=True)
        assert r.set.call_args[0][0] == "dalston:autoscale:tick:dryrun:nemo+pyannote"


class TestFetchFleetSpotSplit:
    def test_spot_flags_populate_fleet_subsets(self, daws, monkeypatch) -> None:
        workers = [
            {
                "instance_id": "i-spot",
                "state": "running",
                "instance_type": "g4dn.xlarge",
                "spot": True,
                "engines": ["nemo", "pyannote"],
                "managed_by": "autoscaler",
                "launch_time": None,
            },
            {
                "instance_id": "i-od-boot",
                "state": "pending",
                "instance_type": "g6.xlarge",
                "spot": False,
                "engines": ["nemo", "pyannote"],
                "managed_by": "autoscaler",
                "launch_time": None,
            },
        ]
        monkeypatch.setattr(daws, "discover_gpu_workers", lambda region: workers)
        monkeypatch.setattr(
            daws,
            "_registry_nodes_by_engine",
            lambda r: {"nemo": {"i-spot"}, "pyannote-4.0": {"i-spot"}},
        )

        fleet, _ = daws._fetch_autoscale_fleet("eu-west-2", MagicMock(), _policy(daws))

        assert fleet.live_instance_ids == ("i-spot",)
        assert fleet.pending_instance_ids == ("i-od-boot",)
        assert fleet.spot_live_instance_ids == ("i-spot",)
        assert fleet.spot_pending_instance_ids == ()
        assert fleet.on_demand_total == 1


# ---------------------------------------------------------------------------
# M95.6: on-demand fallback routing + deprovision
# ---------------------------------------------------------------------------


class TestOnDemandLaunchRouting:
    def test_launch_on_demand_action_forces_use_spot_false(
        self, daws, monkeypatch
    ) -> None:
        import dalston_autoscale as das

        @contextmanager
        def fake_lock():
            yield

        monkeypatch.setattr(daws, "_launch_lock", fake_lock)
        launch = MagicMock(return_value="launched i-od (g6.xlarge, on-demand)")
        monkeypatch.setattr(daws, "_autoscale_launch", launch)
        monkeypatch.setattr(daws, "_clear_blocked_state", MagicMock())

        daws._apply_decision(
            MagicMock(),
            _ctx(daws),
            _policy(daws),
            das.ScaleAction.LAUNCH_ON_DEMAND,
            das.FleetSnapshot(),
            {},
            datetime.now(UTC),
        )

        assert launch.call_args[1]["use_spot"] is False

    def test_plain_launch_still_uses_spot(self, daws, monkeypatch) -> None:
        import dalston_autoscale as das

        @contextmanager
        def fake_lock():
            yield

        monkeypatch.setattr(daws, "_launch_lock", fake_lock)
        launch = MagicMock(return_value="launched i-spot")
        monkeypatch.setattr(daws, "_autoscale_launch", launch)
        monkeypatch.setattr(daws, "_clear_blocked_state", MagicMock())

        daws._apply_decision(
            MagicMock(),
            _ctx(daws),
            _policy(daws),
            das.ScaleAction.LAUNCH,
            das.FleetSnapshot(),
            {},
            datetime.now(UTC),
        )

        assert launch.call_args[1]["use_spot"] is True

    def test_on_demand_launch_failure_still_records_blocked(
        self, daws, monkeypatch
    ) -> None:
        import dalston_autoscale as das

        @contextmanager
        def fake_lock():
            yield

        monkeypatch.setattr(daws, "_launch_lock", fake_lock)
        monkeypatch.setattr(
            daws,
            "_autoscale_launch",
            MagicMock(side_effect=daws.SpotCapacityError("no capacity anywhere")),
        )
        monkeypatch.setattr(daws, "audit", MagicMock())
        write = MagicMock()
        monkeypatch.setattr(daws, "_write_blocked_state", write)

        daws._apply_decision(
            MagicMock(),
            _ctx(daws),
            _policy(daws),
            das.ScaleAction.LAUNCH_ON_DEMAND,
            das.FleetSnapshot(),
            {},
            datetime.now(UTC),
        )
        assert write.called


class TestTerminatePrefersOnDemand:
    def test_on_demand_terminated_before_newer_spot(self, daws, monkeypatch) -> None:
        """On-demand costs ~3x spot — shed the paid worker first even
        though the normal rule is newest-first."""
        import dalston_autoscale as das

        term = MagicMock(return_value="terminated i-od")
        monkeypatch.setattr(daws, "_autoscale_terminate", term)
        monkeypatch.setattr(daws, "_clear_blocked_state", MagicMock())

        fleet = das.FleetSnapshot(
            live_instance_ids=("i-od", "i-spot-newer"),
            spot_live_instance_ids=("i-spot-newer",),
        )
        daws._apply_decision(
            MagicMock(),
            _ctx(daws),
            _policy(daws),
            das.ScaleAction.TERMINATE,
            fleet,
            {"i-od": "autoscaler", "i-spot-newer": "autoscaler"},
            datetime.now(UTC),
        )

        assert term.call_args[0][3] == "i-od"

    def test_all_spot_fleet_keeps_newest_first(self, daws, monkeypatch) -> None:
        import dalston_autoscale as das

        term = MagicMock(return_value="terminated i-spot2")
        monkeypatch.setattr(daws, "_autoscale_terminate", term)
        monkeypatch.setattr(daws, "_clear_blocked_state", MagicMock())

        fleet = das.FleetSnapshot(
            live_instance_ids=("i-spot1", "i-spot2"),
            spot_live_instance_ids=("i-spot1", "i-spot2"),
        )
        daws._apply_decision(
            MagicMock(),
            _ctx(daws),
            _policy(daws),
            das.ScaleAction.TERMINATE,
            fleet,
            {"i-spot1": "autoscaler", "i-spot2": "autoscaler"},
            datetime.now(UTC),
        )

        assert term.call_args[0][3] == "i-spot2"

    def test_manual_on_demand_is_never_terminated(self, daws, monkeypatch) -> None:
        import dalston_autoscale as das

        term = MagicMock(return_value="terminated i-auto-spot")
        monkeypatch.setattr(daws, "_autoscale_terminate", term)
        monkeypatch.setattr(daws, "_clear_blocked_state", MagicMock())

        fleet = das.FleetSnapshot(
            live_instance_ids=("i-manual-od", "i-auto-spot"),
            spot_live_instance_ids=("i-auto-spot",),
        )
        daws._apply_decision(
            MagicMock(),
            _ctx(daws),
            _policy(daws),
            das.ScaleAction.TERMINATE,
            fleet,
            {"i-manual-od": "manual", "i-auto-spot": "autoscaler"},
            datetime.now(UTC),
        )

        assert term.call_args[0][3] == "i-auto-spot"


class TestDeprovision:
    def test_script_removes_timer_and_service(self, daws) -> None:
        script = daws._autoscale_deprovision_script()
        assert "systemctl disable --now dalston-autoscale.timer" in script
        assert "rm -f /etc/systemd/system/dalston-autoscale.timer" in script
        assert "systemctl daemon-reload" in script

    def test_script_clears_every_autoscale_key(self, daws) -> None:
        """A surviving no-TTL shapes marker would leave the console
        reporting a ghost autoscaler forever."""
        script = daws._autoscale_deprovision_script()
        for key in (
            "dalston:autoscale:shapes",
            "dalston:autoscale:overrides",
            "dalston:autoscale:tick:*",
            "dalston:autoscale:blocked:*",
            "dalston:autoscale:state:*",
            "dalston:autoscale:pending:*",
        ):
            assert key in script, f"{key} not cleaned up"

    def test_policy_file_is_left_in_place(self, daws) -> None:
        script = daws._autoscale_deprovision_script()
        assert "rm -f /data/dalston/autoscale-policy.yaml" not in script

    def test_remote_records_autoscale_false_in_state(self, daws, monkeypatch) -> None:
        st = MagicMock()
        st.instance_id = "i-cp"
        st.region = "eu-west-2"
        st.key_name = "dalston-key"
        st.autoscale = True
        monkeypatch.setattr(daws, "require_state", lambda: st)
        monkeypatch.setattr(daws, "get_instance_state", lambda r, i: "running")
        monkeypatch.setattr(daws, "_pipe_provision_script_over_ssh", MagicMock())
        monkeypatch.setattr(daws, "audit", MagicMock())
        save = MagicMock()
        monkeypatch.setattr(daws, "save_state", save)

        daws._autoscale_deprovision_remote()

        assert st.autoscale is False
        save.assert_called_once_with(st)

    def test_relaunch_without_autoscaler_clears_stale_keys(self, daws) -> None:
        """/data (and Redis) survives terminate->relaunch."""
        snippet = daws._autoscale_redis_cleanup_snippet()
        assert "dalston:autoscale:shapes" in snippet
