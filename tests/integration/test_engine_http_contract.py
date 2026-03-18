"""Interface contract tests for engine HTTP APIs (M79).

Validates the HTTP contract defined in ENGINE_COMPOSABILITY §3 against
any engine exposing the ``EngineHTTPServer`` endpoints.  The same tests
run against all three initial engines (onnx-asr, faster-whisper,
diarize-pyannote).

These tests require the engines to be running and accessible at the
configured URLs.  Use ``make dev`` to start the full stack, or run
individual engines locally.
"""

from __future__ import annotations

import pytest

httpx = pytest.importorskip("httpx")

pytestmark = pytest.mark.e2e


@pytest.fixture(
    params=[
        ("onnx", "http://localhost:9100"),
        ("faster-whisper", "http://localhost:9101"),
        ("pyannote-4.0", "http://localhost:9102"),
        ("whisper-pyannote", "http://localhost:9103"),
        ("phoneme-align", "http://localhost:9104"),
    ]
)
def engine_endpoint(request: pytest.FixtureRequest) -> tuple[str, str]:
    """Parametrized fixture yielding (engine_name, base_url) tuples."""
    return request.param


class TestEngineHTTPContract:
    """Validates the engine interface contract from ENGINE_COMPOSABILITY §3."""

    def test_health_returns_status(self, engine_endpoint: tuple[str, str]) -> None:
        _name, url = engine_endpoint
        resp = httpx.get(f"{url}/health", timeout=10)
        assert resp.status_code == 200
        data = resp.json()
        assert "status" in data
        assert data["status"] in ("healthy", "unhealthy")

    def test_capabilities_returns_stages(
        self, engine_endpoint: tuple[str, str]
    ) -> None:
        _name, url = engine_endpoint
        resp = httpx.get(f"{url}/v1/capabilities", timeout=10)
        assert resp.status_code == 200
        data = resp.json()
        assert "stages" in data
        assert isinstance(data["stages"], list)
        assert len(data["stages"]) > 0

    def test_capabilities_returns_engine_id(
        self, engine_endpoint: tuple[str, str]
    ) -> None:
        name, url = engine_endpoint
        resp = httpx.get(f"{url}/v1/capabilities", timeout=10)
        data = resp.json()
        assert "engine_id" in data
        assert data["engine_id"] == name

    def test_metrics_endpoint_exists(self, engine_endpoint: tuple[str, str]) -> None:
        _name, url = engine_endpoint
        resp = httpx.get(f"{url}/metrics", timeout=10)
        assert resp.status_code == 200

    def test_submit_returns_structured_result(
        self, engine_endpoint: tuple[str, str]
    ) -> None:
        """Submit test audio and verify the result format."""
        name, url = engine_endpoint
        caps = httpx.get(f"{url}/v1/capabilities", timeout=10).json()
        stages = caps["stages"]

        if "transcription" in stages or "transcribe" in stages:
            endpoint = "/v1/transcribe"
            form_data = {
                "audio_url": "s3://dalston-artifacts/test/test-audio.wav",
                "language": "en",
            }
        elif "diarisation" in stages or "diarize" in stages:
            endpoint = "/v1/diarize"
            form_data = {
                "audio_url": "s3://dalston-artifacts/test/test-audio.wav",
            }
        elif "alignment" in stages or "align" in stages:
            import json

            endpoint = "/v1/align"
            form_data = {
                "audio_url": "s3://dalston-artifacts/test/test-audio.wav",
                "transcript": json.dumps(
                    {
                        "text": "Hello world",
                        "segments": [{"start": 0.0, "end": 1.0, "text": "Hello world"}],
                        "language": "en",
                    }
                ),
            }
        else:
            pytest.skip(f"No test for stages: {stages}")

        resp = httpx.post(f"{url}{endpoint}", data=form_data, timeout=60)
        assert resp.status_code == 200
        data = resp.json()
        assert "engine_id" in data

    def test_composite_combined_endpoint(
        self, engine_endpoint: tuple[str, str]
    ) -> None:
        """Composite engines expose /v1/transcribe_and_diarize."""
        name, url = engine_endpoint
        caps = httpx.get(f"{url}/v1/capabilities", timeout=10).json()
        stages = caps["stages"]

        has_transcribe = "transcription" in stages or "transcribe" in stages
        has_diarize = "diarisation" in stages or "diarize" in stages

        if not (has_transcribe and has_diarize):
            pytest.skip("Not a composite covering both stages")

        form_data = {
            "audio_url": "s3://dalston-artifacts/test/test-audio.wav",
            "language": "en",
        }
        resp = httpx.post(
            f"{url}/v1/transcribe_and_diarize",
            data=form_data,
            timeout=120,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "engine_id" in data
        assert "stages_completed" in data
