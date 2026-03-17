"""Combined Faster-Whisper + Pyannote composite engine.

A thin entry point that uses the generic ``CompositeEngine`` from the
engine SDK.  All orchestration logic (HTTP dispatch to children, parallel
fan-out, result merging) is handled by the SDK.

Configuration lives entirely in ``engine.yaml`` — the ``compose`` block
declares which child engines to call and which stages they cover.  Child
URLs are resolved from environment variables or Docker-DNS convention.

Environment variables:
    DALSTON_ENGINE_ID: Override engine ID (default from engine.yaml)
    DALSTON_CHILD_URL_FASTER_WHISPER: Override faster-whisper URL
        (default: http://faster-whisper:9100)
    DALSTON_CHILD_URL_PYANNOTE_4_0: Override pyannote URL
        (default: http://pyannote-4-0:9100)
"""

from __future__ import annotations

from dalston.engine_sdk.base_composite import CompositeEngine


class WhisperPyannoteEngine(CompositeEngine):
    """Combined faster-whisper + pyannote-4.0 engine.

    This is purely a named entry point.  The ``CompositeEngine`` base
    class reads ``engine.yaml`` to discover children and handles all
    dispatch and merging.
    """


if __name__ == "__main__":
    engine = WhisperPyannoteEngine()
    engine.run()
