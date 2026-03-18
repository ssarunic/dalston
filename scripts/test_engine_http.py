#!/usr/bin/env python3
"""CLI utility for testing engine HTTP endpoints (M79 contract).

Exercises the HTTP interface of any Dalston engine that implements the
M79 engine HTTP API: ``/health``, ``/v1/capabilities``, and the
stage-specific submit endpoint (``/v1/transcribe``, ``/v1/diarize``,
``/v1/align``, or ``/v1/transcribe_and_diarize`` for composite engines).

Usage:
    # Test a single engine at its default port
    python scripts/test_engine_http.py --url http://localhost:9100

    # Test the combo (whisper-pyannote) engine
    python scripts/test_engine_http.py --url http://localhost:9103 --audio path/to/audio.wav

    # Test against a running Docker stack (all default ports)
    python scripts/test_engine_http.py --url http://localhost:9103 --combined

    # Provide a transcript for align testing
    python scripts/test_engine_http.py --url http://localhost:9104 --audio path/to/audio.wav

Exit code 0 = all checks passed, 1 = one or more failed.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

try:
    import httpx
except ImportError:
    print("ERROR: httpx is required. Install with: pip install httpx")
    sys.exit(1)


# ---------------------------------------------------------------------------
# Colours / helpers
# ---------------------------------------------------------------------------

_GREEN = "\033[32m"
_RED = "\033[31m"
_YELLOW = "\033[33m"
_RESET = "\033[0m"


def _ok(msg: str) -> None:
    print(f"  {_GREEN}✓{_RESET}  {msg}")


def _fail(msg: str) -> None:
    print(f"  {_RED}✗{_RESET}  {msg}")


def _info(msg: str) -> None:
    print(f"     {_YELLOW}{msg}{_RESET}")


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------


def check_health(client: httpx.Client, base_url: str) -> bool:
    print("\nGET /health")
    try:
        resp = client.get(f"{base_url}/health", timeout=10)
        if resp.status_code != 200:
            _fail(f"HTTP {resp.status_code}")
            return False
        data = resp.json()
        if "status" not in data:
            _fail("Response missing 'status' field")
            return False
        status = data["status"]
        if status not in ("healthy", "unhealthy", "degraded"):
            _fail(f"Unexpected status: {status!r}")
            return False
        _ok(f"status={status!r}")
        if "children" in data:
            for child, child_health in data["children"].items():
                _info(f"  child {child}: {child_health.get('status', '?')}")
        return True
    except Exception as e:
        _fail(f"Request failed: {e}")
        return False


def check_capabilities(client: httpx.Client, base_url: str) -> dict | None:
    print("\nGET /v1/capabilities")
    try:
        resp = client.get(f"{base_url}/v1/capabilities", timeout=10)
        if resp.status_code != 200:
            _fail(f"HTTP {resp.status_code}")
            return None
        caps = resp.json()
        if "engine_id" not in caps:
            _fail("Response missing 'engine_id'")
            return None
        if "stages" not in caps or not caps["stages"]:
            _fail("Response missing or empty 'stages'")
            return None
        _ok(f"engine_id={caps['engine_id']!r}  stages={caps['stages']}")
        return caps
    except Exception as e:
        _fail(f"Request failed: {e}")
        return None


def check_metrics(client: httpx.Client, base_url: str) -> bool:
    print("\nGET /metrics")
    try:
        resp = client.get(f"{base_url}/metrics", timeout=10)
        if resp.status_code != 200:
            _fail(f"HTTP {resp.status_code}")
            return False
        _ok("Prometheus metrics endpoint reachable")
        return True
    except Exception as e:
        _fail(f"Request failed: {e}")
        return False


def _make_dummy_wav() -> Path:
    """Write a minimal RIFF/WAV header to a temp file."""
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        # RIFF header + fmt chunk + empty data chunk (mono 16 kHz 16-bit)
        f.write(b"RIFF\x24\x00\x00\x00WAVEfmt ")
        f.write(b"\x10\x00\x00\x00\x01\x00\x01\x00\x80\x3e\x00\x00")
        f.write(b"\x00\x7d\x00\x00\x02\x00\x10\x00")
        f.write(b"data\x00\x00\x00\x00")
        return Path(f.name)


def check_transcribe(
    client: httpx.Client, base_url: str, audio_path: Path, language: str = "en"
) -> bool:
    print(f"\nPOST /v1/transcribe  (audio={audio_path.name})")
    try:
        with audio_path.open("rb") as f:
            resp = client.post(
                f"{base_url}/v1/transcribe",
                data={"language": language},
                files={"file": (audio_path.name, f, "audio/wav")},
                timeout=120,
            )
        if resp.status_code != 200:
            _fail(f"HTTP {resp.status_code}: {resp.text[:200]}")
            return False
        data = resp.json()
        if "engine_id" not in data:
            _fail("Response missing 'engine_id'")
            return False
        _ok(f"engine_id={data['engine_id']!r}  text={str(data.get('text', ''))[:60]!r}")
        return True
    except Exception as e:
        _fail(f"Request failed: {e}")
        return False


def check_diarize(
    client: httpx.Client, base_url: str, audio_path: Path, model: str | None = None
) -> bool:
    print(f"\nPOST /v1/diarize  (audio={audio_path.name})")
    try:
        form: dict = {}
        if model:
            form["model"] = model
        with audio_path.open("rb") as f:
            resp = client.post(
                f"{base_url}/v1/diarize",
                data=form or None,
                files={"file": (audio_path.name, f, "audio/wav")},
                timeout=120,
            )
        if resp.status_code != 200:
            _fail(f"HTTP {resp.status_code}: {resp.text[:200]}")
            return False
        data = resp.json()
        if "engine_id" not in data:
            _fail("Response missing 'engine_id'")
            return False
        _ok(f"engine_id={data['engine_id']!r}")
        return True
    except Exception as e:
        _fail(f"Request failed: {e}")
        return False


def check_align(
    client: httpx.Client,
    base_url: str,
    audio_path: Path,
    transcript: dict | None = None,
    model: str | None = None,
) -> bool:
    print(f"\nPOST /v1/align  (audio={audio_path.name})")
    if transcript is None:
        transcript = {
            "engine_id": "test",
            "text": "Hello world",
            "segments": [{"start": 0.0, "end": 1.0, "text": "Hello world"}],
            "language": "en",
        }
    try:
        form: dict = {"transcript": json.dumps(transcript)}
        if model:
            form["model"] = model
        with audio_path.open("rb") as f:
            resp = client.post(
                f"{base_url}/v1/align",
                data=form,
                files={"file": (audio_path.name, f, "audio/wav")},
                timeout=120,
            )
        if resp.status_code != 200:
            _fail(f"HTTP {resp.status_code}: {resp.text[:200]}")
            return False
        data = resp.json()
        if "engine_id" not in data:
            _fail("Response missing 'engine_id'")
            return False
        _ok(f"engine_id={data['engine_id']!r}")
        return True
    except Exception as e:
        _fail(f"Request failed: {e}")
        return False


def check_transcribe_and_diarize(
    client: httpx.Client,
    base_url: str,
    audio_path: Path,
    language: str = "en",
    diarize_model: str | None = None,
    align_model: str | None = None,
) -> bool:
    print(f"\nPOST /v1/transcribe_and_diarize  (audio={audio_path.name})")
    try:
        form: dict = {"language": language}
        if diarize_model:
            form["model_diarize"] = diarize_model
        if align_model:
            form["model_align"] = align_model
        with audio_path.open("rb") as f:
            resp = client.post(
                f"{base_url}/v1/transcribe_and_diarize",
                data=form,
                files={"file": (audio_path.name, f, "audio/wav")},
                timeout=300,
            )
        if resp.status_code != 200:
            _fail(f"HTTP {resp.status_code}: {resp.text[:200]}")
            return False
        data = resp.json()
        if "engine_id" not in data:
            _fail("Response missing 'engine_id'")
            return False
        if "stages_completed" not in data:
            _fail("Response missing 'stages_completed'")
            return False
        _ok(
            f"engine_id={data['engine_id']!r}  "
            f"stages_completed={data['stages_completed']}"
        )
        if "warnings" in data:
            for w in data["warnings"]:
                _info(f"WARNING: {w}")
        return True
    except Exception as e:
        _fail(f"Request failed: {e}")
        return False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Test Dalston engine HTTP endpoints (M79 contract)"
    )
    parser.add_argument(
        "--url",
        default="http://localhost:9103",
        help="Base URL of the engine (default: http://localhost:9103)",
    )
    parser.add_argument(
        "--audio",
        type=Path,
        default=None,
        help="Path to a WAV audio file for submit tests (default: synthetic dummy)",
    )
    parser.add_argument(
        "--language",
        default="en",
        help="Language code to pass in transcription requests (default: en)",
    )
    parser.add_argument(
        "--combined",
        action="store_true",
        help="Force running /v1/transcribe_and_diarize even if capabilities say otherwise",
    )
    parser.add_argument(
        "--diarize-model",
        default=None,
        help="Model ID to pass to diarize/transcribe_and_diarize requests",
    )
    parser.add_argument(
        "--align-model",
        default=None,
        help="Model ID to pass to align/transcribe_and_diarize requests",
    )
    parser.add_argument(
        "--skip-submit",
        action="store_true",
        help="Skip submit endpoint tests (only test /health, /capabilities, /metrics)",
    )
    args = parser.parse_args()

    base_url = args.url.rstrip("/")
    print(f"\nTesting engine at: {base_url}")
    print("=" * 60)

    results: list[bool] = []

    # Resolve audio file before opening the client so we can bail early
    dummy_wav: Path | None = None
    if not args.skip_submit:
        if args.audio:
            audio_path = args.audio
            if not audio_path.exists():
                print(f"\nERROR: audio file not found: {audio_path}")
                return 1
        else:
            dummy_wav = _make_dummy_wav()
            audio_path = dummy_wav
            _info(f"No --audio provided, using synthetic dummy WAV: {audio_path}")

    with httpx.Client() as client:
        # Core infrastructure endpoints
        results.append(check_health(client, base_url))
        caps = check_capabilities(client, base_url)
        results.append(caps is not None)
        results.append(check_metrics(client, base_url))

        if not args.skip_submit and caps:
            stages = set(caps.get("stages", []))

            if "transcribe" in stages:
                results.append(
                    check_transcribe(client, base_url, audio_path, args.language)
                )

            if "diarize" in stages:
                results.append(
                    check_diarize(
                        client, base_url, audio_path, model=args.diarize_model
                    )
                )

            if "align" in stages:
                results.append(
                    check_align(client, base_url, audio_path, model=args.align_model)
                )

            if ("transcribe" in stages and "diarize" in stages) or args.combined:
                results.append(
                    check_transcribe_and_diarize(
                        client,
                        base_url,
                        audio_path,
                        args.language,
                        args.diarize_model,
                        args.align_model,
                    )
                )

    if dummy_wav:
        dummy_wav.unlink(missing_ok=True)

    passed = sum(results)
    total = len(results)
    print(f"\n{'=' * 60}")
    color = _GREEN if passed == total else _RED
    print(f"Results: {color}{passed}/{total} checks passed{_RESET}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
