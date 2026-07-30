"""Pure decision logic for M91 queue-based GPU autoscaling.

Stdlib-only on purpose: `dalston-aws` (the boto3 CLI this module serves) has
no dalston-package or redis dependency at import time, and this module keeps
the one piece of autoscaling that deserves unit tests — the scaling decision
— importable by pytest without touching AWS or Redis. All I/O (EC2, Redis,
policy file reads) lives in `dalston-aws`; callers build the snapshot
dataclasses and interpret the returned decision.

Semantics (see docs/plan/milestones/M91-queue-based-gpu-autoscaling.md):

- demand for a shape is the max of its per-engine backlogs (one instance
  serves every engine in the shape simultaneously)
- desired = clamp(ceil(max_backlog / tasks_per_instance), min, max)
- one action per tick per shape; a pending (booting) instance suppresses
  further launches (single-flight)
- scale-down only when every engine in the shape is idle (lag == 0 and
  in-flight == 0) sustained for the cooldown; the caller tracks idle_since
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal

DEFAULT_GPU_TYPE_PREFERENCE = ("g4dn.xlarge", "g6.xlarge", "g5.xlarge")

POLICY_SCHEMA_VERSION = 1

# Version of the dalston:autoscale:tick:<shape> JSON snapshot (M95) — a
# public contract read by the gateway's /api/console/nodes. Distinct from
# POLICY_SCHEMA_VERSION: different artifact, different lifecycle. The
# gateway degrades a shape to "stale" on an unknown version rather than
# failing the whole response, so bumping this is safe but deliberate.
AUTOSCALE_TICK_SCHEMA_VERSION = 1


class PolicyError(ValueError):
    """Malformed autoscale policy — fail loud, never scale on garbage."""


@dataclass(frozen=True)
class EngineBacklog:
    engine_id: str
    lag: int  # XINFO GROUPS lag — enqueued, never delivered
    pending: int  # XPENDING summary — delivered, unacked (in flight)

    @property
    def backlog(self) -> int:
        return self.lag + self.pending

    @property
    def idle(self) -> bool:
        return self.lag == 0 and self.pending == 0


@dataclass(frozen=True)
class BacklogSnapshot:
    engines: tuple[EngineBacklog, ...]

    @property
    def max_backlog(self) -> int:
        return max((e.backlog for e in self.engines), default=0)

    @property
    def all_idle(self) -> bool:
        return all(e.idle for e in self.engines)


@dataclass(frozen=True)
class FleetSnapshot:
    """Tag-discovered fleet for one shape, cross-checked against the registry.

    live: every engine of the shape has a fresh registry record on the node.
    pending: tag-visible (pending/running) but not yet fully registered —
    counts toward capacity so a booting worker suppresses further launches.

    The spot_* tuples (M95) are subsets of the corresponding id tuples,
    from EC2's InstanceLifecycle. An instance absent from both spot tuples
    counts as on-demand — fails safe against undercounting paid capacity.
    """

    live_instance_ids: tuple[str, ...] = ()
    pending_instance_ids: tuple[str, ...] = ()
    spot_live_instance_ids: tuple[str, ...] = ()
    spot_pending_instance_ids: tuple[str, ...] = ()

    @property
    def live(self) -> int:
        return len(self.live_instance_ids)

    @property
    def pending(self) -> int:
        return len(self.pending_instance_ids)

    @property
    def total(self) -> int:
        return self.live + self.pending

    @property
    def spot_live(self) -> int:
        return len(self.spot_live_instance_ids)

    @property
    def on_demand_live(self) -> int:
        return self.live - self.spot_live

    @property
    def on_demand_pending(self) -> int:
        return self.pending - len(self.spot_pending_instance_ids)

    @property
    def on_demand_total(self) -> int:
        return self.on_demand_live + self.on_demand_pending


@dataclass(frozen=True)
class ShapePolicy:
    name: str
    engines: tuple[str, ...]  # GPU_ENGINE_PRESETS keys, e.g. ("nemo", "pyannote")
    stream_engine_ids: tuple[
        str, ...
    ]  # registry/stream ids, e.g. ("nemo", "pyannote-4.0")
    tasks_per_instance: int = 20
    min_instances: int = 0
    max_instances: int = 5
    scale_down_after_s: int = 2100
    drain_wait_s: int = 60
    # Reap a running-but-never-registered worker after this long: a wedged
    # boot (bad image, OOM on model load) otherwise counts as pending
    # forever, suppressing all further launches while billing. Sized for
    # image pull + model download on a cold NVMe (worst case well over the
    # 3-8 min happy path).
    boot_timeout_s: int = 1800
    gpu_type_preference: tuple[str, ...] = DEFAULT_GPU_TYPE_PREFERENCE

    @classmethod
    def from_dict(cls, d: dict) -> ShapePolicy:
        try:
            engines = tuple(str(e) for e in d["engines"])
            stream_engine_ids = tuple(str(e) for e in d["stream_engine_ids"])
        except (KeyError, TypeError) as exc:
            raise PolicyError(
                f"shape missing engines/stream_engine_ids: {d!r}"
            ) from exc
        if not engines or not stream_engine_ids:
            raise PolicyError(f"shape has empty engines/stream_engine_ids: {d!r}")
        name = str(d.get("name") or "+".join(sorted(engines)))
        try:
            policy = cls(
                name=name,
                engines=engines,
                stream_engine_ids=stream_engine_ids,
                tasks_per_instance=int(d.get("tasks_per_instance", 20)),
                min_instances=int(d.get("min_instances", 0)),
                max_instances=int(d.get("max_instances", 5)),
                scale_down_after_s=int(d.get("scale_down_after_s", 2100)),
                drain_wait_s=int(d.get("drain_wait_s", 60)),
                boot_timeout_s=int(d.get("boot_timeout_s", 1800)),
                gpu_type_preference=tuple(
                    str(t)
                    for t in d.get("gpu_type_preference", DEFAULT_GPU_TYPE_PREFERENCE)
                ),
            )
        except (TypeError, ValueError) as exc:
            raise PolicyError(
                f"shape '{name}' has a non-numeric threshold: {exc}"
            ) from exc
        if policy.tasks_per_instance < 1:
            raise PolicyError(f"shape '{name}': tasks_per_instance must be >= 1")
        # boot_timeout_s drives termination of pending workers — zero or
        # tiny values would reap every worker the moment it launches.
        if policy.boot_timeout_s < 300:
            raise PolicyError(
                f"shape '{name}': boot_timeout_s must be >= 300 "
                f"(got {policy.boot_timeout_s}); workers need minutes to "
                "boot, join Tailscale, and download models"
            )
        if policy.scale_down_after_s < 0 or policy.drain_wait_s < 0:
            raise PolicyError(
                f"shape '{name}': scale_down_after_s and drain_wait_s must be >= 0"
            )
        if not 0 <= policy.min_instances <= policy.max_instances:
            raise PolicyError(
                f"shape '{name}': need 0 <= min_instances <= max_instances "
                f"(got {policy.min_instances}/{policy.max_instances})"
            )
        if not policy.gpu_type_preference:
            raise PolicyError(f"shape '{name}': gpu_type_preference is empty")
        return policy


def parse_policy(data: dict) -> list[ShapePolicy]:
    """Parse the autoscale-policy YAML structure (already yaml.safe_load'ed)."""
    if not isinstance(data, dict):
        raise PolicyError(f"policy root must be a mapping, got {type(data).__name__}")
    version = data.get("schema_version", POLICY_SCHEMA_VERSION)
    if version != POLICY_SCHEMA_VERSION:
        raise PolicyError(
            f"unsupported policy schema_version {version!r} "
            f"(this build understands {POLICY_SCHEMA_VERSION})"
        )
    shapes_raw = data.get("shapes")
    if not isinstance(shapes_raw, list) or not shapes_raw:
        raise PolicyError("policy must define a non-empty 'shapes' list")
    shapes = [ShapePolicy.from_dict(s) for s in shapes_raw]
    names = [s.name for s in shapes]
    if len(set(names)) != len(names):
        raise PolicyError(f"duplicate shape names in policy: {names}")
    return shapes


class ScaleAction(StrEnum):
    NONE = "none"
    LAUNCH = "launch"
    TERMINATE = "terminate"


@dataclass(frozen=True)
class ScaleDecision:
    shape: str
    action: ScaleAction
    desired: int
    live: int
    pending: int
    max_backlog: int
    reason: str
    backlogs: tuple[EngineBacklog, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict:
        return {
            "shape": self.shape,
            "action": self.action.value,
            "desired": self.desired,
            "live": self.live,
            "pending": self.pending,
            "max_backlog": self.max_backlog,
            "reason": self.reason,
            "per_engine": {
                b.engine_id: {"lag": b.lag, "in_flight": b.pending}
                for b in self.backlogs
            },
        }


@dataclass(frozen=True)
class PolicyEcho:
    """Effective policy values echoed into the tick snapshot (M95).

    The console shows what the autoscaler actually used this tick — after
    settings overrides — making the override plumbing self-verifying.
    """

    tasks_per_instance: int
    min_instances: int
    max_instances: int
    scale_down_after_s: int
    overrides_applied: tuple[str, ...] = ()
    override_error: str | None = None

    @classmethod
    def from_policy(
        cls,
        policy: ShapePolicy,
        *,
        overrides_applied: tuple[str, ...] = (),
        override_error: str | None = None,
    ) -> PolicyEcho:
        return cls(
            tasks_per_instance=policy.tasks_per_instance,
            min_instances=policy.min_instances,
            max_instances=policy.max_instances,
            scale_down_after_s=policy.scale_down_after_s,
            overrides_applied=overrides_applied,
            override_error=override_error,
        )

    def to_dict(self) -> dict:
        return {
            "tasks_per_instance": self.tasks_per_instance,
            "min_instances": self.min_instances,
            "max_instances": self.max_instances,
            "scale_down_after_s": self.scale_down_after_s,
            "overrides_applied": list(self.overrides_applied),
            "override_error": self.override_error,
        }


@dataclass(frozen=True)
class BlockedInfo:
    """Sustained spot-launch failure state, tracked across ticks (M95).

    Lives in its own Redis key (dalston:autoscale:blocked:<shape>) — the
    idle-state hash is deleted whenever backlog exists, which is exactly
    when blocked tracking matters.
    """

    kind: Literal["spot_quota", "spot_capacity"]
    since: str  # ISO timestamp of the first failed tick in this streak
    consecutive_ticks: int
    detail: str

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "since": self.since,
            "consecutive_ticks": self.consecutive_ticks,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class AutoscaleTickSnapshot:
    """The dalston:autoscale:tick:<shape> payload — a versioned public
    contract read by the gateway (M95). Typed construction so a future
    edit can't silently drop or reorder a field via dict mutation.
    """

    ts: str
    decision: ScaleDecision
    applied: str
    spot_live: int
    on_demand_live: int
    policy: PolicyEcho
    blocked: BlockedInfo | None
    idle_since_s: float | None
    stuck_pending: tuple[str, ...] = ()
    schema_version: int = AUTOSCALE_TICK_SCHEMA_VERSION

    def to_dict(self) -> dict:
        d = self.decision.to_dict()
        d.update(
            schema_version=self.schema_version,
            ts=self.ts,
            applied=self.applied,
            spot_live=self.spot_live,
            on_demand_live=self.on_demand_live,
            idle_since_s=self.idle_since_s,
            policy=self.policy.to_dict(),
            blocked=self.blocked.to_dict() if self.blocked else None,
        )
        if self.stuck_pending:
            d["stuck_pending"] = list(self.stuck_pending)
        return d


def desired_instances(policy: ShapePolicy, max_backlog: int) -> int:
    raw = math.ceil(max_backlog / policy.tasks_per_instance) if max_backlog > 0 else 0
    return max(policy.min_instances, min(policy.max_instances, raw))


def decide(
    policy: ShapePolicy,
    backlog: BacklogSnapshot,
    fleet: FleetSnapshot,
    *,
    idle_since_s: float | None,
) -> ScaleDecision:
    """Pure scaling decision — no I/O, no clock reads.

    idle_since_s: seconds the shape has been continuously idle (every engine
    lag == 0 and in-flight == 0), or None when not idle / unknown. The caller
    owns this bookkeeping (Redis-held between ticks).
    """
    desired = desired_instances(policy, backlog.max_backlog)

    def _decision(action: ScaleAction, reason: str) -> ScaleDecision:
        return ScaleDecision(
            shape=policy.name,
            action=action,
            desired=desired,
            live=fleet.live,
            pending=fleet.pending,
            max_backlog=backlog.max_backlog,
            reason=reason,
            backlogs=backlog.engines,
        )

    if desired > fleet.total:
        if fleet.pending > 0:
            return _decision(
                ScaleAction.NONE,
                f"single-flight: {fleet.pending} instance(s) still booting",
            )
        return _decision(
            ScaleAction.LAUNCH,
            f"backlog {backlog.max_backlog} needs {desired} instance(s), have {fleet.total}",
        )

    if fleet.live > desired and backlog.all_idle:
        if idle_since_s is None:
            return _decision(ScaleAction.NONE, "idle, cooldown starting this tick")
        if idle_since_s >= policy.scale_down_after_s:
            return _decision(
                ScaleAction.TERMINATE,
                f"idle {idle_since_s:.0f}s >= cooldown {policy.scale_down_after_s}s",
            )
        return _decision(
            ScaleAction.NONE,
            f"idle {idle_since_s:.0f}s < cooldown {policy.scale_down_after_s}s",
        )

    return _decision(ScaleAction.NONE, "at desired capacity")
