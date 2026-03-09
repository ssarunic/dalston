"""Integration tests for Riva NIM batch transcription.

These tests require a running Riva NIM container and are excluded from
default test runs. Run with: pytest -m gpu

Prerequisites:
    - NGC_API_KEY set
    - make dev-riva running
    - NIM container healthy (first start ~30 min)
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.gpu


@pytest.fixture
def api_base():
    return "http://localhost:8000"


class TestRivaBatchTranscription:
    """End-to-end batch transcription via Riva NIM."""

    @pytest.mark.asyncio
    async def test_batch_transcription_returns_transcript(self, api_base):
        """Submit a WAV file and get a transcript with word timestamps."""
        # Create a minimal valid WAV file (silence)
        import struct
        import wave
        from io import BytesIO

        import httpx

        buf = BytesIO()
        with wave.open(buf, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(16000)
            wav.writeframes(struct.pack("<" + "h" * 16000, *([0] * 16000)))
        wav_bytes = buf.getvalue()

        async with httpx.AsyncClient(base_url=api_base, timeout=120) as client:
            response = await client.post(
                "/v1/audio/transcriptions",
                files={"file": ("test.wav", wav_bytes, "audio/wav")},
                data={"language": "en"},
            )

        assert response.status_code == 200
        data = response.json()
        assert "text" in data

    @pytest.mark.asyncio
    async def test_riva_engine_registered(self, api_base):
        """Verify Riva engine appears in the engine registry."""
        import httpx

        async with httpx.AsyncClient(base_url=api_base, timeout=10) as client:
            response = await client.get("/v1/engines")

        assert response.status_code == 200
        engines = response.json()
        riva_engines = [e for e in engines if e.get("runtime") == "riva"]
        assert len(riva_engines) > 0

    @pytest.mark.asyncio
    async def test_riva_model_seeded_as_ready(self, api_base):
        """Verify Riva model is seeded as ready (not not_downloaded)."""
        import httpx

        async with httpx.AsyncClient(base_url=api_base, timeout=10) as client:
            response = await client.get("/v1/models")

        assert response.status_code == 200
        models = response.json()
        riva_models = [m for m in models if m.get("runtime") == "riva"]
        assert len(riva_models) > 0
        assert riva_models[0]["status"] == "ready"

    @pytest.mark.asyncio
    async def test_riva_model_pull_rejected(self, api_base):
        """Verify pulling an external model returns an error."""
        import httpx

        async with httpx.AsyncClient(base_url=api_base, timeout=10) as client:
            response = await client.post(
                "/v1/models/nvidia%2Fparakeet-ctc-1.1b-riva/pull"
            )

        # Should be rejected (externally managed model)
        assert response.status_code == 409
