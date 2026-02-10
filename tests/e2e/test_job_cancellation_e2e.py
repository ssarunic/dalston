"""End-to-end tests for job cancellation.

These tests require the full Dalston stack to be running:
    docker compose up -d gateway orchestrator redis postgres minio minio-init \
        engine-audio-prepare engine-faster-whisper engine-final-merger

Run with:
    pytest tests/e2e/test_job_cancellation_e2e.py -v -m e2e
"""

import json
import time
from pathlib import Path

import pytest

from tests.e2e.conftest import run_dalston

AUDIO_DIR = Path(__file__).parent.parent / "audio"


def get_job_status(job_id: str) -> dict:
    """Get job status via CLI."""
    result = run_dalston("jobs", "get", job_id, "--json", timeout=30)
    if result.returncode != 0:
        pytest.fail(f"Failed to get job: {result.stderr}")
    return json.loads(result.stdout)


def submit_job(audio_file: str) -> str:
    """Submit a transcription job and return the job ID."""
    result = run_dalston(
        "transcribe",
        str(audio_file),
        "--format",
        "json",
        timeout=30,
    )
    if result.returncode != 0:
        pytest.fail(f"Failed to submit job: {result.stderr}")
    data = json.loads(result.stdout)
    return data["id"]


@pytest.mark.e2e
class TestJobCancellation:
    """E2E tests for job cancellation."""

    def test_cancel_pending_job(self, audio_dir):
        """Test cancelling a job that is still pending."""
        audio_file = audio_dir / "hello_world.wav"
        if not audio_file.exists():
            pytest.skip(f"Test audio file not found: {audio_file}")

        # Submit job
        job_id = submit_job(audio_file)

        # Cancel immediately (job might still be pending or just started)
        result = run_dalston("jobs", "cancel", job_id, "--json", timeout=30)

        # Should succeed (200) - either cancelled or cancelling
        assert result.returncode == 0, f"Cancel failed: {result.stderr}"
        cancel_data = json.loads(result.stdout)
        assert cancel_data["status"] in ("cancelled", "cancelling")

        # Wait a bit and check final status
        time.sleep(2)
        job = get_job_status(job_id)
        assert job["status"] == "cancelled", f"Expected cancelled, got {job['status']}"

    def test_cancel_running_job_waits_for_completion(self, audio_dir):
        """Test that cancelling a running job waits for in-flight tasks."""
        # Use a longer audio file if available for this test
        audio_file = audio_dir / "hello_world.wav"
        if not audio_file.exists():
            pytest.skip(f"Test audio file not found: {audio_file}")

        # Submit job
        job_id = submit_job(audio_file)

        # Wait for job to start running (poll for RUNNING status)
        max_wait = 30
        for _ in range(max_wait):
            job = get_job_status(job_id)
            if job["status"] == "running":
                break
            if job["status"] in ("completed", "failed", "cancelled"):
                pytest.skip(f"Job finished before we could cancel: {job['status']}")
            time.sleep(1)
        else:
            # Job might have completed quickly - that's OK
            job = get_job_status(job_id)
            if job["status"] in ("completed", "failed"):
                pytest.skip(f"Job finished too quickly: {job['status']}")

        # Cancel the running job
        result = run_dalston("jobs", "cancel", job_id, "--json", timeout=30)
        assert result.returncode == 0, f"Cancel failed: {result.stderr}"

        cancel_data = json.loads(result.stdout)
        # Should be either cancelling (tasks running) or cancelled (tasks done)
        assert cancel_data["status"] in ("cancelled", "cancelling")

        # Wait for final cancellation
        max_wait = 60
        for _ in range(max_wait):
            job = get_job_status(job_id)
            if job["status"] == "cancelled":
                break
            if job["status"] not in ("cancelling", "running"):
                pytest.fail(f"Unexpected job status: {job['status']}")
            time.sleep(1)
        else:
            pytest.fail(f"Job did not reach cancelled state within {max_wait}s")

    def test_cancel_completed_job_fails(self, audio_dir):
        """Test that cancelling a completed job returns an error."""
        audio_file = audio_dir / "hello_world.wav"
        if not audio_file.exists():
            pytest.skip(f"Test audio file not found: {audio_file}")

        # Submit and wait for completion
        result = run_dalston(
            "transcribe",
            str(audio_file),
            "--format",
            "json",
            "--wait",
            timeout=180,
        )
        if result.returncode != 0:
            pytest.fail(f"Failed to complete job: {result.stderr}")

        data = json.loads(result.stdout)
        job_id = data["id"]

        # Try to cancel the completed job
        result = run_dalston("jobs", "cancel", job_id, timeout=30)

        # Should fail with exit code 1
        assert result.returncode == 1, "Expected cancel to fail for completed job"
        assert (
            "completed" in result.stderr.lower()
            or "cannot cancel" in result.stderr.lower()
        )

    def test_cancel_idempotency(self, audio_dir):
        """Test that cancelling an already cancelled job returns an error."""
        audio_file = audio_dir / "hello_world.wav"
        if not audio_file.exists():
            pytest.skip(f"Test audio file not found: {audio_file}")

        # Submit job
        job_id = submit_job(audio_file)

        # Cancel first time
        result1 = run_dalston("jobs", "cancel", job_id, timeout=30)
        assert result1.returncode == 0, f"First cancel failed: {result1.stderr}"

        # Wait for cancellation to complete
        time.sleep(2)
        job = get_job_status(job_id)
        if job["status"] == "cancelling":
            # Wait more
            time.sleep(5)

        # Try to cancel again - should fail
        result2 = run_dalston("jobs", "cancel", job_id, timeout=30)
        assert result2.returncode == 1, "Expected second cancel to fail"

    def test_cancel_nonexistent_job_fails(self):
        """Test that cancelling a nonexistent job returns 404."""
        fake_job_id = "00000000-0000-0000-0000-000000000000"

        result = run_dalston("jobs", "cancel", fake_job_id, timeout=30)

        assert result.returncode == 1, "Expected cancel to fail for nonexistent job"
        assert "not found" in result.stderr.lower() or "404" in result.stderr
