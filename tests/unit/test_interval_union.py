"""Unit tests for compute_interval_union_ms."""

from datetime import UTC, datetime, timedelta

from dalston.common.utils import compute_interval_union_ms


def _dt(seconds: float) -> datetime:
    return datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=seconds)


class TestComputeIntervalUnionMs:
    def test_empty_returns_none(self):
        assert compute_interval_union_ms([]) is None

    def test_all_none_endpoints_returns_none(self):
        assert compute_interval_union_ms([(None, None), (None, _dt(5))]) is None

    def test_zero_width_intervals_ignored(self):
        assert compute_interval_union_ms([(_dt(1), _dt(1))]) is None

    def test_single_interval(self):
        assert compute_interval_union_ms([(_dt(0), _dt(5))]) == 5_000

    def test_disjoint_intervals_sum(self):
        # 0-5s and 10-13s → 8s total
        result = compute_interval_union_ms([(_dt(0), _dt(5)), (_dt(10), _dt(13))])
        assert result == 8_000

    def test_fully_overlapping_intervals_counted_once(self):
        # Both tasks wait 0-5s in parallel on different engines → 5s, not 10s
        result = compute_interval_union_ms([(_dt(0), _dt(5)), (_dt(0), _dt(5))])
        assert result == 5_000

    def test_partial_overlap_merged(self):
        # 0-5s and 3-8s → merged into 0-8s → 8s
        result = compute_interval_union_ms([(_dt(0), _dt(5)), (_dt(3), _dt(8))])
        assert result == 8_000

    def test_nested_interval_absorbed(self):
        # 0-10s contains 2-4s → just 10s
        result = compute_interval_union_ms([(_dt(0), _dt(10)), (_dt(2), _dt(4))])
        assert result == 10_000

    def test_mixed_overlapping_and_disjoint(self):
        # Simulates the transcribe/diarize overlap the user raised:
        # transcribe waits 0-5s, diarize waits 3-8s, merge waits 20-22s.
        # Sum would be 5+5+2 = 12s, but real wait is 8s + 2s = 10s.
        result = compute_interval_union_ms(
            [
                (_dt(0), _dt(5)),
                (_dt(3), _dt(8)),
                (_dt(20), _dt(22)),
            ]
        )
        assert result == 10_000

    def test_skips_missing_endpoints(self):
        # Tasks that never started yet (ready_at set, started_at None) are
        # ignored rather than poisoning the result.
        result = compute_interval_union_ms(
            [
                (_dt(0), _dt(5)),
                (_dt(3), None),
                (None, _dt(10)),
            ]
        )
        assert result == 5_000

    def test_negative_intervals_ignored(self):
        # Clock skew or data corruption shouldn't produce negative totals.
        result = compute_interval_union_ms([(_dt(10), _dt(5)), (_dt(20), _dt(22))])
        assert result == 2_000

    def test_sum_would_overcount_vs_union(self):
        # Regression: prior implementation summed per-task wait_ms, which
        # would return 10_000ms here; the union must return 5_000ms.
        a_start, a_end = _dt(0), _dt(5)
        b_start, b_end = _dt(0), _dt(5)
        naive_sum = int((a_end - a_start).total_seconds() * 1000) + int(
            (b_end - b_start).total_seconds() * 1000
        )
        assert naive_sum == 10_000
        assert compute_interval_union_ms([(a_start, a_end), (b_start, b_end)]) == 5_000
