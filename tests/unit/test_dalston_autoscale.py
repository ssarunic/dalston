"""Unit tests for the M91 autoscale decision module (infra/scripts).

dalston_autoscale is stdlib-only and pure (no AWS/Redis I/O), so these
tests construct snapshots directly — no mocks needed.
"""

import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "infra" / "scripts"))

from dalston_autoscale import (  # noqa: E402
    BacklogSnapshot,
    EngineBacklog,
    FleetSnapshot,
    PolicyError,
    ScaleAction,
    ShapePolicy,
    decide,
    desired_instances,
    parse_policy,
)

POLICY_TEMPLATE = (
    Path(__file__).resolve().parents[2]
    / "infra"
    / "templates"
    / "autoscale-policy.yaml"
)


def make_policy(**overrides) -> ShapePolicy:
    base = {
        "name": "nemo+pyannote",
        "engines": ("nemo", "pyannote"),
        "stream_engine_ids": ("nemo", "pyannote-4.0"),
        "tasks_per_instance": 20,
        "min_instances": 0,
        "max_instances": 5,
        "scale_down_after_s": 2100,
    }
    base.update(overrides)
    return ShapePolicy(**base)


def make_backlog(nemo: tuple[int, int] = (0, 0), pyannote: tuple[int, int] = (0, 0)):
    return BacklogSnapshot(
        (
            EngineBacklog("nemo", *nemo),
            EngineBacklog("pyannote-4.0", *pyannote),
        )
    )


def make_fleet(live: int = 0, pending: int = 0) -> FleetSnapshot:
    return FleetSnapshot(
        live_instance_ids=tuple(f"i-live{n}" for n in range(live)),
        pending_instance_ids=tuple(f"i-pend{n}" for n in range(pending)),
    )


class TestParsePolicy:
    def test_repo_template_parses(self):
        shapes = parse_policy(yaml.safe_load(POLICY_TEMPLATE.read_text()))
        assert len(shapes) == 1
        shape = shapes[0]
        assert shape.engines == ("nemo", "pyannote")
        assert shape.stream_engine_ids == ("nemo", "pyannote-4.0")
        assert shape.min_instances == 0
        # g4dn first: operator's AZ has better g4dn spot availability
        assert shape.gpu_type_preference[0] == "g4dn.xlarge"

    def test_bad_schema_version_rejected(self):
        with pytest.raises(PolicyError, match="schema_version"):
            parse_policy({"schema_version": 99, "shapes": []})

    def test_empty_shapes_rejected(self):
        with pytest.raises(PolicyError, match="shapes"):
            parse_policy({"shapes": []})

    def test_missing_engines_rejected(self):
        with pytest.raises(PolicyError, match="engines"):
            parse_policy({"shapes": [{"stream_engine_ids": ["nemo"]}]})

    def test_min_greater_than_max_rejected(self):
        with pytest.raises(PolicyError, match="min_instances"):
            parse_policy(
                {
                    "shapes": [
                        {
                            "engines": ["nemo"],
                            "stream_engine_ids": ["nemo"],
                            "min_instances": 3,
                            "max_instances": 1,
                        }
                    ]
                }
            )

    @pytest.mark.parametrize("value", ["false", "no", "off", "0", 0, 1, None])
    def test_non_boolean_fallback_flag_rejected(self, value):
        """bool("false") is True — a quoted YAML string must never authorise
        paid instances. Cost controls fail closed and loudly."""
        with pytest.raises(PolicyError, match="fallback_to_on_demand"):
            parse_policy(
                {
                    "shapes": [
                        {
                            "engines": ["nemo"],
                            "stream_engine_ids": ["nemo"],
                            "fallback_to_on_demand": value,
                        }
                    ]
                }
            )

    @pytest.mark.parametrize("value", [True, False])
    def test_real_booleans_accepted(self, value):
        shapes = parse_policy(
            {
                "shapes": [
                    {
                        "engines": ["nemo"],
                        "stream_engine_ids": ["nemo"],
                        "fallback_to_on_demand": value,
                    }
                ]
            }
        )
        assert shapes[0].fallback_to_on_demand is value

    def test_zero_tasks_per_instance_rejected(self):
        with pytest.raises(PolicyError, match="tasks_per_instance"):
            parse_policy(
                {
                    "shapes": [
                        {
                            "engines": ["nemo"],
                            "stream_engine_ids": ["nemo"],
                            "tasks_per_instance": 0,
                        }
                    ]
                }
            )

    def test_duplicate_shape_names_rejected(self):
        shape = {"engines": ["nemo"], "stream_engine_ids": ["nemo"]}
        with pytest.raises(PolicyError, match="duplicate"):
            parse_policy({"shapes": [shape, dict(shape)]})


class TestDesiredInstances:
    def test_zero_backlog_scales_to_min(self):
        assert desired_instances(make_policy(), 0) == 0
        assert desired_instances(make_policy(min_instances=1), 0) == 1

    def test_ceil_division(self):
        policy = make_policy()
        assert desired_instances(policy, 1) == 1
        assert desired_instances(policy, 20) == 1
        assert desired_instances(policy, 21) == 2

    def test_clamped_to_max(self):
        assert desired_instances(make_policy(), 10_000) == 5


class TestDecide:
    def test_backlog_with_empty_fleet_launches(self):
        d = decide(
            make_policy(), make_backlog(nemo=(5, 0)), make_fleet(), idle_since_s=None
        )
        assert d.action == ScaleAction.LAUNCH
        assert d.desired == 1

    def test_demand_is_max_of_engines_not_sum(self):
        # 15 + 10 across engines still fits one instance (max=15, not 25)
        d = decide(
            make_policy(),
            make_backlog(nemo=(15, 0), pyannote=(10, 0)),
            make_fleet(live=1),
            idle_since_s=None,
        )
        assert d.action == ScaleAction.NONE

    def test_in_flight_counts_toward_backlog(self):
        d = decide(
            make_policy(), make_backlog(nemo=(0, 3)), make_fleet(), idle_since_s=None
        )
        assert d.action == ScaleAction.LAUNCH

    def test_pending_instance_suppresses_launch(self):
        d = decide(
            make_policy(),
            make_backlog(nemo=(50, 0)),
            make_fleet(pending=1),
            idle_since_s=None,
        )
        assert d.action == ScaleAction.NONE
        assert "single-flight" in d.reason

    def test_launch_when_backlog_exceeds_live_capacity(self):
        d = decide(
            make_policy(),
            make_backlog(nemo=(45, 0)),
            make_fleet(live=1),
            idle_since_s=None,
        )
        assert d.action == ScaleAction.LAUNCH
        assert d.desired == 3

    def test_idle_before_cooldown_holds(self):
        d = decide(
            make_policy(), make_backlog(), make_fleet(live=1), idle_since_s=100.0
        )
        assert d.action == ScaleAction.NONE

    def test_idle_past_cooldown_terminates(self):
        d = decide(
            make_policy(), make_backlog(), make_fleet(live=1), idle_since_s=2100.0
        )
        assert d.action == ScaleAction.TERMINATE

    def test_idle_unknown_starts_cooldown_not_terminate(self):
        d = decide(make_policy(), make_backlog(), make_fleet(live=1), idle_since_s=None)
        assert d.action == ScaleAction.NONE

    def test_no_terminate_while_any_engine_busy(self):
        # live > desired-by-nemo but pyannote still has in-flight work
        d = decide(
            make_policy(),
            make_backlog(pyannote=(0, 1)),
            make_fleet(live=2),
            idle_since_s=10_000.0,
        )
        assert d.action != ScaleAction.TERMINATE

    def test_min_instances_floor_prevents_scale_to_zero(self):
        d = decide(
            make_policy(min_instances=1),
            make_backlog(),
            make_fleet(live=1),
            idle_since_s=10_000.0,
        )
        assert d.action == ScaleAction.NONE

    def test_empty_fleet_idle_is_noop(self):
        d = decide(make_policy(), make_backlog(), make_fleet(), idle_since_s=None)
        assert d.action == ScaleAction.NONE

    def test_to_dict_reports_per_engine(self):
        d = decide(
            make_policy(), make_backlog(nemo=(2, 1)), make_fleet(), idle_since_s=None
        )
        payload = d.to_dict()
        assert payload["per_engine"]["nemo"] == {"lag": 2, "in_flight": 1}
        assert payload["action"] == "launch"


class TestBootTimeoutValidation:
    def test_default_boot_timeout(self):
        assert make_policy().boot_timeout_s == 1800

    def test_negative_boot_timeout_rejected(self):
        with pytest.raises(PolicyError, match="boot_timeout_s"):
            parse_policy(
                {
                    "shapes": [
                        {
                            "engines": ["nemo"],
                            "stream_engine_ids": ["nemo"],
                            "boot_timeout_s": -1,
                        }
                    ]
                }
            )

    def test_too_small_boot_timeout_rejected(self):
        # zero/tiny values would reap every worker the moment it launches
        with pytest.raises(PolicyError, match="boot_timeout_s"):
            parse_policy(
                {
                    "shapes": [
                        {
                            "engines": ["nemo"],
                            "stream_engine_ids": ["nemo"],
                            "boot_timeout_s": 60,
                        }
                    ]
                }
            )

    def test_minimum_boot_timeout_accepted(self):
        shapes = parse_policy(
            {
                "shapes": [
                    {
                        "engines": ["nemo"],
                        "stream_engine_ids": ["nemo"],
                        "boot_timeout_s": 300,
                    }
                ]
            }
        )
        assert shapes[0].boot_timeout_s == 300

    def test_negative_cooldown_rejected(self):
        with pytest.raises(PolicyError, match="scale_down_after_s"):
            parse_policy(
                {
                    "shapes": [
                        {
                            "engines": ["nemo"],
                            "stream_engine_ids": ["nemo"],
                            "scale_down_after_s": -5,
                        }
                    ]
                }
            )

    def test_negative_drain_wait_rejected(self):
        with pytest.raises(PolicyError, match="drain_wait_s"):
            parse_policy(
                {
                    "shapes": [
                        {
                            "engines": ["nemo"],
                            "stream_engine_ids": ["nemo"],
                            "drain_wait_s": -1,
                        }
                    ]
                }
            )


# ---------------------------------------------------------------------------
# M95: fleet lifecycle split + tick snapshot contract
# ---------------------------------------------------------------------------

from dalston_autoscale import (  # noqa: E402
    AUTOSCALE_TICK_SCHEMA_VERSION,
    AutoscaleTickSnapshot,
    BlockedInfo,
    PolicyEcho,
)


class TestFleetSnapshotLifecycle:
    def test_defaults_count_everything_as_on_demand(self):
        """Unknown lifecycle fails safe: it counts against the paid cap."""
        fleet = FleetSnapshot(live_instance_ids=("i-1", "i-2"))
        assert fleet.spot_live == 0
        assert fleet.on_demand_live == 2
        assert fleet.on_demand_total == 2

    def test_spot_subsets_split_the_counts(self):
        fleet = FleetSnapshot(
            live_instance_ids=("i-1", "i-2", "i-3"),
            pending_instance_ids=("i-4", "i-5"),
            spot_live_instance_ids=("i-1", "i-2"),
            spot_pending_instance_ids=("i-4",),
        )
        assert fleet.spot_live == 2
        assert fleet.on_demand_live == 1
        assert fleet.on_demand_pending == 1
        assert fleet.on_demand_total == 2

    def test_all_spot_fleet_has_zero_on_demand(self):
        fleet = FleetSnapshot(
            live_instance_ids=("i-1",),
            spot_live_instance_ids=("i-1",),
        )
        assert fleet.on_demand_total == 0


def _make_snapshot(**overrides) -> AutoscaleTickSnapshot:
    policy = make_policy()
    decision = decide(
        policy,
        make_backlog(nemo=(41, 0)),
        make_fleet(live=2),
        idle_since_s=None,
    )
    base = {
        "ts": "2026-07-30T12:04:00+00:00",
        "decision": decision,
        "applied": "at desired capacity",
        "spot_live": 2,
        "on_demand_live": 0,
        "policy": PolicyEcho.from_policy(policy),
        "blocked": None,
        "idle_since_s": None,
    }
    base.update(overrides)
    return AutoscaleTickSnapshot(**base)


class TestAutoscaleTickSnapshot:
    def test_decision_fields_survive_untouched(self):
        d = _make_snapshot().to_dict()
        assert d["shape"] == "nemo+pyannote"
        assert d["max_backlog"] == 41
        assert d["desired"] == 3
        assert d["live"] == 2
        assert d["per_engine"]["nemo"] == {"lag": 41, "in_flight": 0}

    def test_snapshot_contract_fields(self):
        d = _make_snapshot().to_dict()
        assert d["schema_version"] == AUTOSCALE_TICK_SCHEMA_VERSION
        assert d["ts"] == "2026-07-30T12:04:00+00:00"
        assert d["applied"] == "at desired capacity"
        assert d["spot_live"] == 2
        assert d["on_demand_live"] == 0
        assert d["blocked"] is None
        assert d["idle_since_s"] is None
        assert d["policy"] == {
            "tasks_per_instance": 20,
            "min_instances": 0,
            "max_instances": 5,
            "scale_down_after_s": 2100,
            "overrides_applied": [],
            "override_error": None,
        }

    def test_stuck_pending_omitted_when_empty(self):
        assert "stuck_pending" not in _make_snapshot().to_dict()
        d = _make_snapshot(stuck_pending=("i-stuck",)).to_dict()
        assert d["stuck_pending"] == ["i-stuck"]

    def test_blocked_state_serialized(self):
        blocked = BlockedInfo(
            kind="spot_quota",
            since="2026-07-30T11:58:00+00:00",
            consecutive_ticks=6,
            detail="MaxSpotInstanceCountExceeded",
        )
        d = _make_snapshot(blocked=blocked).to_dict()
        assert d["blocked"] == {
            "kind": "spot_quota",
            "since": "2026-07-30T11:58:00+00:00",
            "consecutive_ticks": 6,
            "detail": "MaxSpotInstanceCountExceeded",
        }

    def test_json_round_trip(self):
        import json as _json

        parsed = _json.loads(_json.dumps(_make_snapshot().to_dict()))
        assert parsed["schema_version"] == AUTOSCALE_TICK_SCHEMA_VERSION


class TestPolicyEcho:
    def test_from_policy_echoes_effective_values(self):
        echo = PolicyEcho.from_policy(
            make_policy(tasks_per_instance=10),
            overrides_applied=("tasks_per_instance",),
            override_error=None,
        )
        assert echo.tasks_per_instance == 10
        assert echo.to_dict()["overrides_applied"] == ["tasks_per_instance"]


# ---------------------------------------------------------------------------
# M95.5: settings overrides layered onto the YAML policy
# ---------------------------------------------------------------------------

from dalston_autoscale import OVERRIDE_KEYS, apply_overrides  # noqa: E402


class TestApplyOverrides:
    def test_no_overrides_returns_policy_unchanged(self):
        policy = make_policy()
        effective, applied, error = apply_overrides(policy, {})
        assert effective is policy
        assert applied == ()
        assert error is None

    def test_valid_override_applied_and_named(self):
        effective, applied, error = apply_overrides(
            make_policy(), {"tasks_per_instance": "10"}
        )
        assert effective.tasks_per_instance == 10
        assert applied == ("tasks_per_instance",)
        assert error is None

    def test_untouched_fields_keep_yaml_values(self):
        policy = make_policy(max_instances=3, boot_timeout_s=1800)
        effective, _, _ = apply_overrides(policy, {"tasks_per_instance": "10"})
        assert effective.max_instances == 3
        assert effective.boot_timeout_s == 1800
        assert effective.gpu_type_preference == policy.gpu_type_preference
        assert effective.name == policy.name

    def test_unknown_hash_fields_ignored(self):
        effective, applied, error = apply_overrides(
            make_policy(), {"nonsense": "5", "fallback_to_on_demand": "true"}
        )
        assert applied == ()
        assert error is None
        assert effective.tasks_per_instance == 20

    def test_cost_fields_are_not_overridable(self):
        """YAML-only by design — enabling paid fallback must not be a
        console knob."""
        assert "fallback_to_on_demand" not in OVERRIDE_KEYS
        assert "max_on_demand" not in OVERRIDE_KEYS

    def test_invalid_combination_discards_all_overrides(self):
        """min > max across YAML/override boundary: discard everything,
        never partially apply, keep scaling on the YAML."""
        policy = make_policy(max_instances=3)
        effective, applied, error = apply_overrides(
            policy, {"min_instances": "4", "tasks_per_instance": "10"}
        )
        assert effective is policy
        assert applied == ()
        assert error is not None
        assert "min_instances" in error
        # the valid sibling override was discarded too
        assert effective.tasks_per_instance == 20

    def test_non_numeric_override_is_reported_not_raised(self):
        policy = make_policy()
        effective, applied, error = apply_overrides(
            policy, {"tasks_per_instance": "not-a-number"}
        )
        assert effective is policy
        assert applied == ()
        assert error is not None

    def test_out_of_range_override_rejected(self):
        policy = make_policy()
        effective, applied, error = apply_overrides(policy, {"tasks_per_instance": "0"})
        assert effective is policy
        assert error is not None

    def test_multiple_valid_overrides_all_applied(self):
        effective, applied, error = apply_overrides(
            make_policy(),
            {"tasks_per_instance": "10", "min_instances": "1", "max_instances": "3"},
        )
        assert error is None
        assert set(applied) == {"tasks_per_instance", "min_instances", "max_instances"}
        assert (effective.tasks_per_instance, effective.min_instances) == (10, 1)
        assert effective.max_instances == 3

    def test_override_changes_desired_instances(self):
        """The whole point: a console change alters scaling this tick."""
        base = make_policy()
        effective, _, _ = apply_overrides(base, {"tasks_per_instance": "10"})
        assert desired_instances(base, 41) == 3
        assert desired_instances(effective, 41) == 5


# ---------------------------------------------------------------------------
# M95.6: opt-in on-demand fallback after sustained spot failures
# ---------------------------------------------------------------------------

from dalston_autoscale import ON_DEMAND_FALLBACK_BLOCKED_TICKS  # noqa: E402

BLOCKED = ON_DEMAND_FALLBACK_BLOCKED_TICKS


def _fallback_fleet(on_demand_live: int = 0, on_demand_pending: int = 0):
    """Fleet with only on-demand instances (spot subsets left empty)."""
    return FleetSnapshot(
        live_instance_ids=tuple(f"i-od-live{n}" for n in range(on_demand_live)),
        pending_instance_ids=tuple(f"i-od-boot{n}" for n in range(on_demand_pending)),
    )


class TestOnDemandFallback:
    def test_disabled_by_default_never_escalates(self):
        """Cost safety: an unconfigured policy must never launch paid
        capacity no matter how long spot has been blocked."""
        d = decide(
            make_policy(),
            make_backlog(nemo=(41, 0)),
            make_fleet(),
            idle_since_s=None,
            blocked_ticks=999,
        )
        assert d.action == ScaleAction.LAUNCH

    def test_cooldown_after_failed_fallback_re_probes_spot(self):
        """A failed paid fallback must not pin the shape to the paid path.

        While the on-demand cooldown is set the tick falls back through to a
        spot launch, so spot keeps being tested; otherwise an on-demand
        outage would stop the shape launching anything at all.
        """
        d = decide(
            make_policy(fallback_to_on_demand=True),
            make_backlog(nemo=(41, 0)),
            make_fleet(),
            idle_since_s=None,
            blocked_ticks=BLOCKED,
            on_demand_cooldown=True,
        )
        assert d.action == ScaleAction.LAUNCH

    def test_escalates_again_once_cooldown_expires(self):
        d = decide(
            make_policy(fallback_to_on_demand=True),
            make_backlog(nemo=(41, 0)),
            make_fleet(),
            idle_since_s=None,
            blocked_ticks=BLOCKED,
            on_demand_cooldown=False,
        )
        assert d.action == ScaleAction.LAUNCH_ON_DEMAND

    def test_escalates_at_the_threshold(self):
        d = decide(
            make_policy(fallback_to_on_demand=True),
            make_backlog(nemo=(41, 0)),
            make_fleet(),
            idle_since_s=None,
            blocked_ticks=BLOCKED,
        )
        assert d.action == ScaleAction.LAUNCH_ON_DEMAND
        assert "falling back to on-demand" in d.reason

    def test_below_threshold_stays_on_spot(self):
        d = decide(
            make_policy(fallback_to_on_demand=True),
            make_backlog(nemo=(41, 0)),
            make_fleet(),
            idle_since_s=None,
            blocked_ticks=BLOCKED - 1,
        )
        assert d.action == ScaleAction.LAUNCH

    def test_no_blocked_streak_stays_on_spot(self):
        d = decide(
            make_policy(fallback_to_on_demand=True),
            make_backlog(nemo=(41, 0)),
            make_fleet(),
            idle_since_s=None,
        )
        assert d.action == ScaleAction.LAUNCH

    def test_pending_on_demand_worker_blocks_a_second_launch(self):
        """The regression this guards: a fallback launched last tick is
        still booting this tick, and must already count against the cap or
        we double-launch paid capacity."""
        d = decide(
            make_policy(fallback_to_on_demand=True, max_on_demand=1),
            make_backlog(nemo=(999, 0)),
            _fallback_fleet(on_demand_pending=1),
            idle_since_s=None,
            blocked_ticks=BLOCKED,
        )
        # single-flight also applies — either way, no second paid launch
        assert d.action != ScaleAction.LAUNCH_ON_DEMAND

    def test_live_on_demand_worker_blocks_a_second_launch(self):
        d = decide(
            make_policy(fallback_to_on_demand=True, max_on_demand=1),
            make_backlog(nemo=(999, 0)),
            _fallback_fleet(on_demand_live=1),
            idle_since_s=None,
            blocked_ticks=BLOCKED,
        )
        assert d.action == ScaleAction.LAUNCH

    def test_higher_cap_allows_a_second_launch(self):
        d = decide(
            make_policy(fallback_to_on_demand=True, max_on_demand=2),
            make_backlog(nemo=(999, 0)),
            _fallback_fleet(on_demand_live=1),
            idle_since_s=None,
            blocked_ticks=BLOCKED,
        )
        assert d.action == ScaleAction.LAUNCH_ON_DEMAND

    def test_spot_fleet_does_not_consume_on_demand_headroom(self):
        fleet = FleetSnapshot(
            live_instance_ids=("i-spot",),
            spot_live_instance_ids=("i-spot",),
        )
        d = decide(
            make_policy(fallback_to_on_demand=True, max_on_demand=1),
            make_backlog(nemo=(999, 0)),
            fleet,
            idle_since_s=None,
            blocked_ticks=BLOCKED,
        )
        assert d.action == ScaleAction.LAUNCH_ON_DEMAND

    def test_at_desired_capacity_never_escalates(self):
        """Fallback is a spot-vs-paid choice, not a capacity decision."""
        d = decide(
            make_policy(fallback_to_on_demand=True),
            make_backlog(nemo=(5, 0)),
            make_fleet(live=1),
            idle_since_s=None,
            blocked_ticks=BLOCKED,
        )
        assert d.action == ScaleAction.NONE

    def test_max_on_demand_zero_disables_escalation(self):
        d = decide(
            make_policy(fallback_to_on_demand=True, max_on_demand=0),
            make_backlog(nemo=(41, 0)),
            make_fleet(),
            idle_since_s=None,
            blocked_ticks=BLOCKED,
        )
        assert d.action == ScaleAction.LAUNCH


class TestFallbackPolicyParsing:
    def test_defaults_are_off(self):
        policy = ShapePolicy.from_dict(
            {"engines": ["nemo"], "stream_engine_ids": ["nemo"]}
        )
        assert policy.fallback_to_on_demand is False
        assert policy.max_on_demand == 1

    def test_parsed_from_yaml_dict(self):
        policy = ShapePolicy.from_dict(
            {
                "engines": ["nemo"],
                "stream_engine_ids": ["nemo"],
                "fallback_to_on_demand": True,
                "max_on_demand": 2,
            }
        )
        assert policy.fallback_to_on_demand is True
        assert policy.max_on_demand == 2

    def test_negative_max_on_demand_rejected(self):
        with pytest.raises(PolicyError, match="max_on_demand"):
            ShapePolicy.from_dict(
                {
                    "engines": ["nemo"],
                    "stream_engine_ids": ["nemo"],
                    "max_on_demand": -1,
                }
            )

    def test_template_parses_with_fallback_documented(self):
        shapes = parse_policy(yaml.safe_load(POLICY_TEMPLATE.read_text()))
        assert shapes[0].fallback_to_on_demand is False
        assert shapes[0].max_on_demand == 1

    def test_overrides_preserve_fallback_config(self):
        """apply_overrides rebuilds the policy — cost config must survive
        or a console knob change would silently disable paid fallback."""
        policy = make_policy(fallback_to_on_demand=True, max_on_demand=2)
        effective, applied, error = apply_overrides(
            policy, {"tasks_per_instance": "10"}
        )
        assert error is None
        assert applied == ("tasks_per_instance",)
        assert effective.fallback_to_on_demand is True
        assert effective.max_on_demand == 2
