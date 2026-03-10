"""Unit tests for AdmissionController edge cases.

Validates the QoS admission policy that prevents realtime session
starvation under batch load.
"""

from __future__ import annotations

import threading

import pytest

from dalston.engine_sdk.admission import AdmissionConfig, AdmissionController


@pytest.fixture
def config() -> AdmissionConfig:
    """Default test config: 2 RT reserved, 4 batch max, 6 total."""
    return AdmissionConfig(rt_reservation=2, batch_max_inflight=4, total_capacity=6)


@pytest.fixture
def controller(config: AdmissionConfig) -> AdmissionController:
    return AdmissionController(config)


class TestBatchAdmission:
    """Batch admission respects RT reservation and inflight cap."""

    def test_accept_batch_when_empty(self, controller: AdmissionController) -> None:
        assert controller.can_accept_batch() is True
        assert controller.admit_batch() is True

    def test_reject_batch_at_inflight_cap(
        self, controller: AdmissionController
    ) -> None:
        # Fill to batch cap (4)
        for _ in range(4):
            assert controller.admit_batch() is True

        # 5th batch should be rejected (cap=4)
        assert controller.can_accept_batch() is False
        assert controller.admit_batch() is False

    def test_batch_respects_rt_reservation(self) -> None:
        # Total=4, RT reservation=2, batch max=4
        # With no RT sessions, batch should only use 2 slots (leaving 2 for RT)
        config = AdmissionConfig(
            rt_reservation=2, batch_max_inflight=4, total_capacity=4
        )
        controller = AdmissionController(config)

        assert controller.admit_batch() is True
        assert controller.admit_batch() is True

        # 3rd batch would eat into RT reservation
        assert controller.can_accept_batch() is False
        assert controller.admit_batch() is False

    def test_batch_can_use_rt_slots_when_rt_has_reservation(self) -> None:
        # Total=6, RT reservation=2, batch max=4
        # If 2 RT sessions are active, batch can use remaining 4 slots
        config = AdmissionConfig(
            rt_reservation=2, batch_max_inflight=4, total_capacity=6
        )
        controller = AdmissionController(config)

        # Fill RT reservation
        assert controller.admit_rt() is True
        assert controller.admit_rt() is True

        # Now batch can use all 4 slots since RT has its reservation
        for _ in range(4):
            assert controller.admit_batch() is True

        # At capacity
        assert controller.can_accept_batch() is False

    def test_release_batch_makes_slot_available(
        self, controller: AdmissionController
    ) -> None:
        for _ in range(4):
            controller.admit_batch()

        assert controller.can_accept_batch() is False

        controller.release_batch()
        assert controller.can_accept_batch() is True

    def test_release_batch_clamps_to_zero(
        self, controller: AdmissionController
    ) -> None:
        # Release without any active should not go negative
        controller.release_batch()
        status = controller.get_status()
        assert status["active_batch"] == 0


class TestRTAdmission:
    """RT admission uses total capacity."""

    def test_accept_rt_when_empty(self, controller: AdmissionController) -> None:
        assert controller.can_accept_rt() is True
        assert controller.admit_rt() is True

    def test_rt_gets_reserved_slots_under_batch_load(self) -> None:
        # Total=4, RT=2, Batch=4
        config = AdmissionConfig(
            rt_reservation=2, batch_max_inflight=4, total_capacity=4
        )
        controller = AdmissionController(config)

        # Fill all batch slots (only 2 allowed due to RT reservation)
        controller.admit_batch()
        controller.admit_batch()

        # RT should still be able to use its reserved slots
        assert controller.can_accept_rt() is True
        assert controller.admit_rt() is True
        assert controller.admit_rt() is True

        # Now at full capacity
        assert controller.can_accept_rt() is False

    def test_reject_rt_at_full_capacity(self) -> None:
        config = AdmissionConfig(
            rt_reservation=2, batch_max_inflight=2, total_capacity=4
        )
        controller = AdmissionController(config)

        # Fill to total capacity with mix
        controller.admit_batch()
        controller.admit_batch()
        controller.admit_rt()
        controller.admit_rt()

        assert controller.can_accept_rt() is False
        assert controller.admit_rt() is False

    def test_release_rt_makes_slot_available(
        self, controller: AdmissionController
    ) -> None:
        # Fill to capacity
        for _ in range(6):
            controller.admit_rt()

        assert controller.can_accept_rt() is False

        controller.release_rt()
        assert controller.can_accept_rt() is True

    def test_release_rt_clamps_to_zero(self, controller: AdmissionController) -> None:
        controller.release_rt()
        status = controller.get_status()
        assert status["active_rt"] == 0


class TestMixedLoad:
    """Mixed batch + RT load scenarios."""

    def test_mixed_load_respects_all_constraints(self) -> None:
        config = AdmissionConfig(
            rt_reservation=2, batch_max_inflight=3, total_capacity=5
        )
        controller = AdmissionController(config)

        # Admit 1 RT session
        assert controller.admit_rt() is True

        # Batch can use slots while still leaving room for RT reservation.
        # State: 1 RT, 0 batch. Available=4, RT shortfall=1.
        # available(4) > shortfall(1) → batch OK
        assert controller.admit_batch() is True
        # State: 1 RT, 1 batch. Available=3, RT shortfall=1. batch OK
        assert controller.admit_batch() is True
        # State: 1 RT, 2 batch. Available=2, RT shortfall=1. batch OK
        # (still 1 slot left for RT after admitting this batch)
        assert controller.admit_batch() is True

        # State: 1 RT, 3 batch. At batch cap (3). No more batch.
        assert controller.can_accept_batch() is False

        # RT can still get its reserved slot
        assert controller.admit_rt() is True

        # Fully loaded (5/5)
        assert controller.can_accept_batch() is False
        assert controller.can_accept_rt() is False

    def test_release_order_independence(self, controller: AdmissionController) -> None:
        controller.admit_batch()
        controller.admit_rt()
        controller.admit_batch()

        # Release in different order
        controller.release_rt()
        controller.release_batch()

        status = controller.get_status()
        assert status["active_batch"] == 1
        assert status["active_rt"] == 0


class TestStatus:
    """Status reporting."""

    def test_status_reflects_current_state(
        self, controller: AdmissionController
    ) -> None:
        controller.admit_batch()
        controller.admit_rt()

        status = controller.get_status()
        assert status["active_batch"] == 1
        assert status["active_rt"] == 1
        assert status["total_active"] == 2
        assert status["available"] == 4
        assert status["total_capacity"] == 6
        assert status["rt_reservation"] == 2
        assert status["batch_max_inflight"] == 4


class TestThreadSafety:
    """Basic thread safety verification."""

    def test_concurrent_admissions_dont_exceed_capacity(self) -> None:
        config = AdmissionConfig(
            rt_reservation=2, batch_max_inflight=10, total_capacity=10
        )
        controller = AdmissionController(config)

        admitted = {"batch": 0, "rt": 0}
        lock = threading.Lock()

        def admit_batch():
            for _ in range(20):
                if controller.admit_batch():
                    with lock:
                        admitted["batch"] += 1

        def admit_rt():
            for _ in range(20):
                if controller.admit_rt():
                    with lock:
                        admitted["rt"] += 1

        threads = [
            threading.Thread(target=admit_batch),
            threading.Thread(target=admit_batch),
            threading.Thread(target=admit_rt),
            threading.Thread(target=admit_rt),
        ]

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        total = admitted["batch"] + admitted["rt"]
        assert total <= config.total_capacity


class TestConfigFromEnv:
    """Config loading from environment."""

    def test_from_env_defaults(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Clear any existing env vars
        monkeypatch.delenv("DALSTON_RT_RESERVATION", raising=False)
        monkeypatch.delenv("DALSTON_BATCH_MAX_INFLIGHT", raising=False)
        monkeypatch.delenv("DALSTON_TOTAL_CAPACITY", raising=False)

        config = AdmissionConfig.from_env()
        assert config.rt_reservation == 2
        assert config.batch_max_inflight == 4
        assert config.total_capacity == 6

    def test_from_env_custom(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DALSTON_RT_RESERVATION", "3")
        monkeypatch.setenv("DALSTON_BATCH_MAX_INFLIGHT", "5")
        monkeypatch.setenv("DALSTON_TOTAL_CAPACITY", "8")

        config = AdmissionConfig.from_env()
        assert config.rt_reservation == 3
        assert config.batch_max_inflight == 5
        assert config.total_capacity == 8
