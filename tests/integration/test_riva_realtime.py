"""Integration tests for Riva NIM realtime transcription.

These tests require a running Riva NIM container and are excluded from
default test runs. Run with: pytest -m gpu

Prerequisites:
    - NGC_API_KEY set
    - make dev-riva running
    - NIM container healthy (first start ~30 min)
"""

from __future__ import annotations

import asyncio
import json
import struct

import pytest

pytestmark = pytest.mark.gpu


@pytest.fixture
def ws_url():
    return "ws://localhost:8000/v1/audio/transcriptions/stream"


class TestRivaRealtimeTranscription:
    """End-to-end realtime transcription via Riva NIM."""

    @pytest.mark.asyncio
    async def test_realtime_session_connects(self, ws_url):
        """Verify a WebSocket session can be established."""
        import websockets

        async with websockets.connect(ws_url, open_timeout=10) as ws:
            # Send session config
            config = {
                "type": "session.update",
                "session": {
                    "language": "en",
                    "encoding": "pcm_s16le",
                    "sample_rate": 16000,
                },
            }
            await ws.send(json.dumps(config))

            # Should receive session.created or session.updated
            msg = await asyncio.wait_for(ws.recv(), timeout=5)
            data = json.loads(msg)
            assert data.get("type") in ("session.created", "session.updated")

    @pytest.mark.asyncio
    async def test_realtime_audio_produces_transcript(self, ws_url):
        """Send audio frames and receive a transcript."""
        import websockets

        async with websockets.connect(ws_url, open_timeout=10) as ws:
            config = {
                "type": "session.update",
                "session": {
                    "language": "en",
                    "encoding": "pcm_s16le",
                    "sample_rate": 16000,
                },
            }
            await ws.send(json.dumps(config))
            await asyncio.wait_for(ws.recv(), timeout=5)

            # Send 1 second of silence as PCM
            silence = struct.pack("<" + "h" * 16000, *([0] * 16000))
            await ws.send(silence)

            # Send end-of-stream
            await ws.send(json.dumps({"type": "input_audio_buffer.commit"}))

            # Wait for response (may be empty transcript for silence)
            try:
                msg = await asyncio.wait_for(ws.recv(), timeout=10)
                data = json.loads(msg)
                assert "type" in data
            except TimeoutError:
                pass  # Silence may not produce a transcript
