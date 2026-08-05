"""Tests for realtime "Any available" model auto-selection (M102).

The hard constraint is that a model's engine has a live realtime worker.
``native_streaming`` only ranks candidates: cache-aware RNNT/TDT decoding
is preferable, but the realtime SDK also serves non-native models through
VAD segmentation, so they are usable.

Before M102 the flag was a gate. Because almost no model YAML declares
it, the gate excluded nearly everything and the fallback then dropped the
realtime constraint entirely — routing to the largest downloaded model,
which in production was an 18.7 GB engine with no realtime worker at all.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

# The production rule itself — not a copy of it. `select_rt_model` was
# extracted precisely so these tests and the gateway share one
# implementation and cannot drift.
from dalston.gateway.api.v1._realtime_common import select_rt_model


@dataclass
class _Model:
    """Stand-in for a ModelRegistry entry, with the fields routing reads."""

    id: str
    engine_id: str
    native_streaming: bool = False
    size_bytes: int = 0
    languages: list[str] | None = field(default=None)


def _select(models: list[_Model], live_rt_engine_ids: set[str], language: str = "auto"):
    return select_rt_model(models, live_rt_engine_ids, language=language)


# The exact fleet observed in production on 2026-08-05.
_PRODUCTION_MODELS = [
    _Model("Systran/faster-whisper-large-v3", "faster-whisper", False, 3_091_000_000),
    _Model("istupakov/parakeet-tdt-0.6b-v3-onnx", "onnx", False, 3_220_000_000),
    _Model("nvidia/parakeet-tdt-0.6b-v3", "nemo", True, 2_509_000_000),
    _Model("vllm-asr-voxtral-mini-3b", "vllm-asr", False, 18_720_000_000),
]


class TestServableConstraint:
    def test_production_case_picks_the_engine_with_a_worker(self) -> None:
        """The regression: a NeMo worker is live, nothing else is."""
        best = _select(_PRODUCTION_MODELS, live_rt_engine_ids={"nemo"})

        assert best is not None
        assert best.id == "nvidia/parakeet-tdt-0.6b-v3"
        assert best.engine_id == "nemo"

    def test_largest_model_is_rejected_when_its_engine_cannot_serve(self) -> None:
        """vllm-asr is 7x larger but has no realtime worker."""
        best = _select(_PRODUCTION_MODELS, live_rt_engine_ids={"nemo"})

        assert best is not None
        assert best.engine_id != "vllm-asr", (
            "selected an engine with no realtime worker — allocation would fail"
        )

    def test_non_native_model_is_selected_when_its_engine_serves(self) -> None:
        """Non-native models run through the SDK's VAD wrapper, so they count."""
        models = [
            _Model("Systran/faster-whisper-large-v3", "faster-whisper", False, 3_000),
            _Model("nvidia/parakeet-tdt-0.6b-v3", "nemo", True, 2_500),
        ]

        best = _select(models, live_rt_engine_ids={"faster-whisper"})

        assert best is not None
        assert best.id == "Systran/faster-whisper-large-v3"
        assert best.native_streaming is False

    def test_no_servable_engine_returns_nothing(self) -> None:
        """Caller raises a ValueError naming the cause rather than guessing."""
        assert _select(_PRODUCTION_MODELS, live_rt_engine_ids=set()) is None

    def test_no_models_at_all_returns_nothing(self) -> None:
        assert _select([], live_rt_engine_ids={"nemo"}) is None


class TestRanking:
    def test_native_beats_non_native_regardless_of_size(self) -> None:
        models = [
            _Model("big-wrapped", "nemo", False, 20_000_000_000),
            _Model("small-native", "nemo", True, 500_000_000),
        ]

        best = _select(models, live_rt_engine_ids={"nemo"})

        assert best is not None
        assert best.id == "small-native"

    def test_larger_wins_within_the_same_tier(self) -> None:
        """Preserves the pre-existing accuracy heuristic once constrained."""
        models = [
            _Model("small-native", "nemo", True, 500),
            _Model("large-native", "nemo", True, 2_500),
        ]

        best = _select(models, live_rt_engine_ids={"nemo"})

        assert best is not None
        assert best.id == "large-native"

    def test_larger_wins_among_non_native_too(self) -> None:
        models = [
            _Model("small-wrapped", "nemo", False, 500),
            _Model("large-wrapped", "nemo", False, 2_500),
        ]

        best = _select(models, live_rt_engine_ids={"nemo"})

        assert best is not None
        assert best.id == "large-wrapped"


class TestLanguageFiltering:
    def test_language_narrows_within_servable(self) -> None:
        models = [
            _Model("english-only", "nemo", True, 3_000, languages=["en"]),
            _Model("french-only", "nemo", True, 9_000, languages=["fr"]),
        ]

        best = _select(models, live_rt_engine_ids={"nemo"}, language="en")

        assert best is not None
        assert best.id == "english-only"

    def test_language_never_widens_past_servable(self) -> None:
        """A perfect language match on an unservable engine must not win."""
        models = [
            _Model(
                "wrong-engine-right-lang", "vllm-asr", True, 9_000, languages=["en"]
            ),
            _Model("right-engine-wrong-lang", "nemo", True, 1_000, languages=["fr"]),
        ]

        best = _select(models, live_rt_engine_ids={"nemo"}, language="en")

        assert best is not None
        assert best.engine_id == "nemo", (
            "language filtering widened past engines that can serve"
        )

    def test_unmatched_language_falls_back_within_servable(self) -> None:
        """Falls back to servable so the caller's language check can report it."""
        models = [
            _Model("french-only", "nemo", True, 1_000, languages=["fr"]),
        ]

        best = _select(models, live_rt_engine_ids={"nemo"}, language="en")

        assert best is not None
        assert best.engine_id == "nemo"

    def test_models_without_language_list_are_treated_as_universal(self) -> None:
        models = [_Model("any-language", "nemo", True, 1_000, languages=None)]

        best = _select(models, live_rt_engine_ids={"nemo"}, language="ja")

        assert best is not None
        assert best.id == "any-language"


class TestPreM102Regression:
    """The old rule, shown failing on the production fleet."""

    @staticmethod
    def _old_select(models: list[_Model], live_rt_engine_ids: set[str]):
        rt_models = [
            m
            for m in models
            if m.native_streaming and m.engine_id in live_rt_engine_ids
        ]
        candidates = rt_models if rt_models else list(models)
        if not candidates:
            return None
        return max(candidates, key=lambda m: m.size_bytes or 0)

    def test_old_rule_routed_to_an_engine_with_no_worker(self) -> None:
        """Documents the bug: with every flag false, the fallback picked
        the largest model overall, whose engine had no realtime worker."""
        all_flags_false = [
            _Model(m.id, m.engine_id, False, m.size_bytes) for m in _PRODUCTION_MODELS
        ]

        old = self._old_select(all_flags_false, live_rt_engine_ids={"nemo"})
        new = _select(all_flags_false, live_rt_engine_ids={"nemo"})

        assert old is not None and old.engine_id == "vllm-asr"  # the failure
        assert new is not None and new.engine_id == "nemo"  # the fix


@pytest.mark.parametrize(
    "yaml_name,expected",
    [
        ("parakeet-tdt-0.6b-v3", True),
        ("parakeet-tdt-1.1b", True),
        ("parakeet-rnnt-0.6b", True),
        ("parakeet-ctc-0.6b", False),
        ("parakeet-ctc-1.1b", False),
    ],
)
def test_nemo_model_yaml_declares_streaming_by_decoder(
    yaml_name: str, expected: bool
) -> None:
    """NeMo TDT/RNNT decoders stream; CTC cannot.

    Mirrors NeMoInference.STREAMING_DECODER_TYPES == {"rnnt", "tdt"}. The
    YAML previously contradicted the engine for TDT models, which cost the
    native-streaming preference (M102.3).
    """
    from pathlib import Path

    import yaml

    root = Path(__file__).resolve().parents[2]
    data = yaml.safe_load((root / "models" / f"{yaml_name}.yaml").read_text())
    caps = data.get("capabilities", {})

    assert caps.get("native_streaming") is expected, (
        f"{yaml_name}: native_streaming should be {expected} for its decoder type"
    )
