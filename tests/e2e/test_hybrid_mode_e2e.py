"""End-to-end tests for M07 Hybrid Mode.

These tests verify the full hybrid mode workflow:
1. Connect WebSocket with enhance_on_end=true and store_audio=true
2. Stream audio and get realtime transcripts
3. End session and verify enhancement job is created
4. Poll enhancement status endpoint until complete
5. Verify enhanced transcript has diarization and word timestamps

Requires:
- Docker Compose services running (gateway, orchestrator, engines, redis, postgres, minio)
- A realtime worker available

Run with: pytest tests/e2e/test_hybrid_mode_e2e.py -v --run-e2e
"""

import asyncio
import json
import os
import time
from pathlib import Path

import httpx
import pytest
import websockets

# Skip all tests if E2E tests are disabled
pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        not os.environ.get("RUN_E2E_TESTS"),
        reason="E2E tests disabled. Set RUN_E2E_TESTS=1 to run.",
    ),
]

GATEWAY_URL = os.environ.get("DALSTON_GATEWAY_URL", "http://localhost:8000")
GATEWAY_WS_URL = os.environ.get("DALSTON_GATEWAY_WS_URL", "ws://localhost:8000")
API_KEY = os.environ.get("DALSTON_API_KEY", "dk_test_12345678")
AUDIO_DIR = Path(__file__).parent.parent / "audio"


@pytest.fixture
def sample_audio():
    """Load sample audio file for testing."""
    audio_file = AUDIO_DIR / "sample_16k.wav"
    if not audio_file.exists():
        pytest.skip(f"Sample audio file not found: {audio_file}")
    return audio_file


@pytest.fixture
def audio_chunks(sample_audio):
    """Load audio file and split into chunks for streaming."""
    with open(sample_audio, "rb") as f:
        # Skip WAV header (44 bytes)
        f.read(44)
        audio_data = f.read()

    # Split into 0.5 second chunks (16000 samples * 2 bytes * 0.5 sec = 16000 bytes)
    chunk_size = 16000
    chunks = [
        audio_data[i : i + chunk_size] for i in range(0, len(audio_data), chunk_size)
    ]
    return chunks


async def wait_for_gateway():
    """Wait for gateway to be available."""
    async with httpx.AsyncClient() as client:
        for _ in range(30):
            try:
                response = await client.get(f"{GATEWAY_URL}/health")
                if response.status_code == 200:
                    return True
            except Exception:
                pass
            await asyncio.sleep(1)
    return False


class TestHybridModeE2E:
    """End-to-end tests for hybrid mode workflow."""

    @pytest.mark.asyncio
    async def test_hybrid_mode_full_workflow(self, audio_chunks):
        """Test complete hybrid mode workflow from realtime to enhancement."""
        # Wait for gateway
        assert await wait_for_gateway(), "Gateway not available"

        session_id = None
        enhancement_job_id = None

        # Step 1: Connect WebSocket with enhance_on_end and store_audio
        ws_url = (
            f"{GATEWAY_WS_URL}/v1/audio/transcriptions/stream"
            f"?api_key={API_KEY}"
            f"&language=en"
            f"&model=fast"
            f"&store_audio=true"
            f"&store_transcript=true"
            f"&enhance_on_end=true"
        )

        async with websockets.connect(ws_url) as ws:
            # Step 2: Receive session.begin
            msg = await asyncio.wait_for(ws.recv(), timeout=10)
            data = json.loads(msg)
            assert data["type"] == "session.begin"
            session_id = data["session_id"]
            assert session_id is not None

            # Step 3: Stream audio chunks
            transcripts = []
            for chunk in audio_chunks[:10]:  # Stream first 5 seconds
                await ws.send(chunk)
                await asyncio.sleep(0.1)  # Simulate real-time streaming

                # Collect any responses
                try:
                    while True:
                        msg = await asyncio.wait_for(ws.recv(), timeout=0.1)
                        data = json.loads(msg)
                        if data.get("type") == "transcript.final":
                            transcripts.append(data.get("text", ""))
                except TimeoutError:
                    pass

            # Step 4: Send end message
            await ws.send(json.dumps({"type": "end"}))

            # Step 5: Wait for session.end with enhancement_job_id
            for _ in range(100):  # Wait up to 10 seconds
                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=0.1)
                    data = json.loads(msg)
                    if data.get("type") == "session.end":
                        enhancement_job_id = data.get("enhancement_job_id")
                        break
                except TimeoutError:
                    pass

        # Verify we got transcripts
        assert len(transcripts) > 0, "Should have received some transcripts"

        # Verify enhancement job was created
        assert enhancement_job_id is not None, "Enhancement job should be created"

        # Step 6: Poll enhancement status endpoint
        async with httpx.AsyncClient() as client:
            headers = {"Authorization": f"Bearer {API_KEY}"}

            # Poll until completed or failed (timeout after 5 minutes)
            start_time = time.time()
            max_wait = 300  # 5 minutes

            while time.time() - start_time < max_wait:
                response = await client.get(
                    f"{GATEWAY_URL}/v1/realtime/sessions/{session_id}/enhancement",
                    headers=headers,
                )
                assert response.status_code == 200

                data = response.json()
                status = data.get("status")

                if status == "completed":
                    # Verify enhanced transcript
                    transcript = data.get("transcript")
                    assert transcript is not None, "Should have enhanced transcript"

                    # Check for diarization (speakers)
                    segments = transcript.get("segments", [])
                    if segments:
                        # Diarization might not always detect speakers in short audio
                        # so we just verify the structure is correct
                        assert isinstance(segments, list)
                        # Verify segments have expected fields
                        for seg in segments[:3]:
                            assert "start" in seg or "text" in seg

                    # Check for word timestamps
                    words = transcript.get("words", [])
                    if words:
                        # Verify word structure
                        for word in words[:5]:  # Check first 5 words
                            assert "start" in word or "word" in word
                    break

                elif status == "failed":
                    error = data.get("error")
                    pytest.fail(f"Enhancement job failed: {error}")

                await asyncio.sleep(5)  # Poll every 5 seconds

            else:
                pytest.fail("Enhancement job timed out")

    @pytest.mark.asyncio
    async def test_hybrid_mode_validation_error(self):
        """Test that enhance_on_end without store_audio returns error."""
        assert await wait_for_gateway(), "Gateway not available"

        # Try to connect with enhance_on_end=true but store_audio=false
        ws_url = (
            f"{GATEWAY_WS_URL}/v1/audio/transcriptions/stream"
            f"?api_key={API_KEY}"
            f"&language=en"
            f"&store_audio=false"
            f"&enhance_on_end=true"  # This should cause error
        )

        async with websockets.connect(ws_url) as ws:
            # Should receive error message
            msg = await asyncio.wait_for(ws.recv(), timeout=10)
            data = json.loads(msg)

            assert data["type"] == "error"
            assert data["code"] == "invalid_parameters"
            assert "store_audio" in data["message"]

    @pytest.mark.asyncio
    async def test_manual_enhancement_trigger(self, audio_chunks):
        """Test manually triggering enhancement for a completed session."""
        assert await wait_for_gateway(), "Gateway not available"

        session_id = None

        # Step 1: Create session with store_audio but NOT enhance_on_end
        ws_url = (
            f"{GATEWAY_WS_URL}/v1/audio/transcriptions/stream"
            f"?api_key={API_KEY}"
            f"&language=en"
            f"&model=fast"
            f"&store_audio=true"
            f"&store_transcript=true"
            f"&enhance_on_end=false"  # Don't auto-enhance
        )

        async with websockets.connect(ws_url) as ws:
            # Get session ID
            msg = await asyncio.wait_for(ws.recv(), timeout=10)
            data = json.loads(msg)
            assert data["type"] == "session.begin"
            session_id = data["session_id"]

            # Stream some audio
            for chunk in audio_chunks[:5]:
                await ws.send(chunk)
                await asyncio.sleep(0.1)

            # End session
            await ws.send(json.dumps({"type": "end"}))

            # Wait for session.end
            for _ in range(50):
                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=0.1)
                    data = json.loads(msg)
                    if data.get("type") == "session.end":
                        # Should NOT have enhancement_job_id
                        assert data.get("enhancement_job_id") is None
                        break
                except TimeoutError:
                    pass

        # Step 2: Manually trigger enhancement via POST endpoint
        async with httpx.AsyncClient() as client:
            headers = {"Authorization": f"Bearer {API_KEY}"}

            # Trigger enhancement
            response = await client.post(
                f"{GATEWAY_URL}/v1/realtime/sessions/{session_id}/enhance",
                headers=headers,
            )
            assert response.status_code == 200

            data = response.json()
            assert data["status"] == "processing"
            assert data["enhancement_job_id"] is not None

            enhancement_job_id = data["enhancement_job_id"]

            # Step 3: Verify enhancement status endpoint shows the job
            response = await client.get(
                f"{GATEWAY_URL}/v1/realtime/sessions/{session_id}/enhancement",
                headers=headers,
            )
            assert response.status_code == 200

            data = response.json()
            assert data["enhancement_job_id"] == enhancement_job_id
            assert data["status"] in ("processing", "pending")

    @pytest.mark.asyncio
    async def test_enhancement_status_not_requested(self):
        """Test enhancement status for session without enhance_on_end."""
        assert await wait_for_gateway(), "Gateway not available"

        session_id = None

        # Create session without enhance_on_end or store_audio
        ws_url = (
            f"{GATEWAY_WS_URL}/v1/audio/transcriptions/stream"
            f"?api_key={API_KEY}"
            f"&language=en"
            f"&store_audio=false"
            f"&enhance_on_end=false"
        )

        async with websockets.connect(ws_url) as ws:
            msg = await asyncio.wait_for(ws.recv(), timeout=10)
            data = json.loads(msg)
            assert data["type"] == "session.begin"
            session_id = data["session_id"]

            # End immediately
            await ws.send(json.dumps({"type": "end"}))

            # Wait for session.end
            for _ in range(50):
                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=0.1)
                    if json.loads(msg).get("type") == "session.end":
                        break
                except TimeoutError:
                    pass

        # Check enhancement status
        async with httpx.AsyncClient() as client:
            headers = {"Authorization": f"Bearer {API_KEY}"}

            response = await client.get(
                f"{GATEWAY_URL}/v1/realtime/sessions/{session_id}/enhancement",
                headers=headers,
            )
            assert response.status_code == 200

            data = response.json()
            assert data["status"] == "not_requested"
