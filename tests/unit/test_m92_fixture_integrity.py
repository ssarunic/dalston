"""Guard the M92.1 narrowband fixture's diagnostic properties.

The fixture exists to reproduce the incident call's shape on GPU: stereo,
8 kHz, ~30 s, with essentially no energy above 2 kHz (telephony low-pass).
If it gets regenerated or replaced, these properties must hold or the
92.1 diagnosis loses its point.
"""

from pathlib import Path

import numpy as np
import pytest

FIXTURE = Path("tests/fixtures/audio/stereo_8k_narrowband.wav")

sf = pytest.importorskip("soundfile")


@pytest.fixture(scope="module")
def fixture_audio():
    if not FIXTURE.exists():
        pytest.skip("narrowband fixture not present")
    data, rate = sf.read(str(FIXTURE), dtype="float32")
    return data, rate


class TestNarrowbandFixture:
    def test_format(self, fixture_audio):
        data, rate = fixture_audio
        assert rate == 8000
        assert data.ndim == 2 and data.shape[1] == 2
        assert 25.0 <= data.shape[0] / rate <= 35.0

    def test_both_channels_carry_speech(self, fixture_audio):
        data, _ = fixture_audio
        for ch in range(2):
            rms = float(np.sqrt(np.mean(np.square(data[:, ch]))))
            assert rms > 0.005, f"channel {ch} appears silent"

    def test_high_band_is_empty(self, fixture_audio):
        # Telephony low-pass signature: negligible energy above 2 kHz.
        data, rate = fixture_audio
        mono = data.mean(axis=1)
        spectrum = np.abs(np.fft.rfft(mono)) ** 2
        freqs = np.fft.rfftfreq(len(mono), d=1.0 / rate)
        total = float(spectrum.sum())
        high = float(spectrum[freqs >= 2000.0].sum())
        assert total > 0
        assert high / total < 0.01, "fixture lost its narrowband character"
