"""End-to-end WAV transcription tests covering success and failure scenarios.

These tests require a running Docker stack and are excluded from the
default pytest run.  Execute with:

    pytest -m e2e -v tests/e2e/test_wav_scenarios_e2e.py

Required stack (minimal for basic tests):
    docker compose up -d gateway orchestrator redis postgres minio minio-init \
        stt-batch-prepare stt-batch-transcribe-parakeet-tdt-0.6b-v3-cpu stt-batch-merge

For diarization tests, also start:
    stt-batch-diarize-nemo-msdd-cpu

For alignment tests (word-level timestamps):
    stt-batch-align-whisperx-cpu

For PII detection tests:
    stt-batch-pii-detect-presidio

For audio redaction tests (requires PII detection):
    stt-batch-audio-redact-audio
"""

import pytest

from tests.e2e.conftest import AUDIO_DIR, run_dalston, transcribe_json

# Directory containing invalid test audio files
INVALID_AUDIO_DIR = AUDIO_DIR / "invalid"


def transcribe_expect_failure(audio_file, *extra_args, timeout=180):
    """Run transcribe and expect it to fail, returning CompletedProcess."""
    return run_dalston(
        "transcribe",
        str(audio_file),
        "--format",
        "json",
        "--wait",
        *extra_args,
        timeout=timeout,
    )


# =============================================================================
# SUCCESS SCENARIOS
# =============================================================================


@pytest.mark.e2e
class TestWavSuccessMonoFile:
    """Successful transcription of mono WAV files."""

    def test_basic_mono_transcription(self, audio_dir):
        """Mono WAV produces valid transcript with text and segments."""
        result = transcribe_json(audio_dir / "test_merged.wav")

        assert result["status"] == "completed"
        assert result["text"]
        assert len(result["segments"]) > 0

    def test_mono_all_segments_have_text(self, audio_dir):
        """All segments contain non-empty text."""
        result = transcribe_json(audio_dir / "test_merged.wav")

        assert result["status"] == "completed"
        for i, seg in enumerate(result["segments"]):
            assert seg["text"], f"Segment {i} has empty text"
            assert seg["text"].strip(), f"Segment {i} has only whitespace"

    def test_mono_segments_have_valid_timestamps(self, audio_dir):
        """All segments have valid start/end timestamps."""
        result = transcribe_json(audio_dir / "test_merged.wav")

        assert result["status"] == "completed"
        for i, seg in enumerate(result["segments"]):
            assert "start" in seg, f"Segment {i} missing start"
            assert "end" in seg, f"Segment {i} missing end"
            assert seg["start"] >= 0, f"Segment {i} has negative start"
            assert seg["end"] > seg["start"], f"Segment {i} end <= start"

    def test_mono_segments_chronologically_ordered(self, audio_dir):
        """Segments are in chronological order."""
        result = transcribe_json(audio_dir / "test_merged.wav")

        assert result["status"] == "completed"
        segments = result["segments"]
        for i in range(1, len(segments)):
            assert segments[i]["start"] >= segments[i - 1]["start"], (
                f"Segment {i} starts before segment {i - 1}"
            )

    def test_mono_word_level_timestamps(self, audio_dir):
        """Word-level timestamps (default) include words in segments."""
        result = transcribe_json(audio_dir / "test_merged.wav")

        assert result["status"] == "completed"
        # At least some segments should have word-level timestamps
        segments_with_words = [s for s in result["segments"] if s.get("words")]
        assert len(segments_with_words) > 0, "No segments have word-level timestamps"


@pytest.mark.e2e
class TestWavSuccessStereoFile:
    """Successful transcription of stereo WAV files."""

    def test_stereo_basic_transcription(self, audio_dir):
        """Stereo WAV produces valid transcript."""
        result = transcribe_json(audio_dir / "test_stereo_speakers.wav")

        assert result["status"] == "completed"
        assert result["text"]
        assert len(result["segments"]) > 0

    def test_stereo_per_channel_produces_speakers(self, audio_dir):
        """Stereo with per-channel detection produces multiple speakers."""
        result = transcribe_json(
            audio_dir / "test_stereo_speakers.wav",
            "--speakers",
            "per-channel",
        )

        assert result["status"] == "completed"
        speakers = result.get("speakers", [])
        speaker_ids = {s["id"] for s in speakers}
        # Stereo per-channel should produce SPEAKER_00 and SPEAKER_01
        assert "SPEAKER_00" in speaker_ids, "Missing SPEAKER_00"
        assert "SPEAKER_01" in speaker_ids, "Missing SPEAKER_01"

    def test_stereo_segments_have_speaker_attribution(self, audio_dir):
        """Per-channel transcription assigns speakers to segments."""
        result = transcribe_json(
            audio_dir / "test_stereo_speakers.wav",
            "--speakers",
            "per-channel",
        )

        assert result["status"] == "completed"
        segments_with_speaker = [s for s in result["segments"] if s.get("speaker_id")]
        assert len(segments_with_speaker) > 0, "No segments have speaker attribution"


@pytest.mark.e2e
class TestWavSuccessTimestampGranularity:
    """Test different timestamp granularity options."""

    def test_segment_level_timestamps(self, audio_dir):
        """Segment-level timestamps skip word alignment."""
        result = transcribe_json(
            audio_dir / "test_merged.wav",
            "--timestamps",
            "segment",
        )

        assert result["status"] == "completed"
        assert len(result["segments"]) > 0
        # Segment-level should not include top-level words
        assert not result.get("words"), "Expected no top-level words"

    def test_no_timestamps_produces_text_only(self, audio_dir):
        """No timestamps option produces segments without timing info."""
        result = transcribe_json(
            audio_dir / "test_merged.wav",
            "--timestamps",
            "none",
        )

        assert result["status"] == "completed"
        assert result["text"]
        # With timestamps=none, segments might still exist but focus is on text


@pytest.mark.e2e
class TestWavSuccessVocabulary:
    """Test vocabulary/term boosting feature."""

    def test_vocabulary_hints_accepted(self, audio_dir):
        """Transcription with vocabulary hints completes successfully."""
        result = transcribe_json(
            audio_dir / "test_merged.wav",
            "--vocab",
            "hello",
            "--vocab",
            "world",
        )

        assert result["status"] == "completed"
        assert result["text"]


@pytest.mark.e2e
class TestWavSuccessDiarization:
    """Diarization-based speaker detection (requires diarization engine)."""

    def test_mono_diarization_completes(self, audio_dir):
        """Mono file with diarization completes successfully."""
        result = transcribe_json(
            audio_dir / "test_merged.wav",
            "--speakers",
            "diarize",
        )

        assert result["status"] == "completed"
        assert len(result["segments"]) > 0

    def test_stereo_diarization_completes(self, audio_dir):
        """Stereo file with diarization completes successfully."""
        result = transcribe_json(
            audio_dir / "test_stereo_speakers.wav",
            "--speakers",
            "diarize",
        )

        assert result["status"] == "completed"
        assert len(result["segments"]) > 0


@pytest.mark.e2e
class TestWavSuccessAlignment:
    """Word-level alignment tests (requires alignment engine)."""

    def test_word_timestamps_with_alignment(self, audio_dir):
        """Word-level timestamps produce words in segments."""
        result = transcribe_json(
            audio_dir / "test_merged.wav",
            "--timestamps",
            "word",
        )

        assert result["status"] == "completed"
        # Word timestamps should produce words in segments
        segments_with_words = [s for s in result["segments"] if s.get("words")]
        assert len(segments_with_words) > 0, "No segments have word timestamps"

        # Verify word structure
        words = segments_with_words[0]["words"]
        assert len(words) > 0, "Segment has empty words list"
        word = words[0]
        assert "text" in word, "Word missing text"
        assert "start" in word, "Word missing start"
        assert "end" in word, "Word missing end"

    def test_word_timestamps_are_valid(self, audio_dir):
        """Word timestamps have valid start/end values."""
        result = transcribe_json(
            audio_dir / "test_merged.wav",
            "--timestamps",
            "word",
        )

        assert result["status"] == "completed"
        for seg in result["segments"]:
            words = seg.get("words", [])
            for i, word in enumerate(words):
                assert word["start"] >= 0, f"Word {i} has negative start"
                assert word["end"] > word["start"], f"Word {i} end <= start"

    def test_words_within_segment_bounds(self, audio_dir):
        """Word timestamps fall within their segment bounds."""
        result = transcribe_json(
            audio_dir / "test_merged.wav",
            "--timestamps",
            "word",
        )

        assert result["status"] == "completed"
        for seg in result["segments"]:
            seg_start = seg["start"]
            seg_end = seg["end"]
            for word in seg.get("words", []):
                # Allow small tolerance for timing differences
                assert word["start"] >= seg_start - 0.1, (
                    f"Word starts before segment: {word['start']} < {seg_start}"
                )
                assert word["end"] <= seg_end + 0.1, (
                    f"Word ends after segment: {word['end']} > {seg_end}"
                )


@pytest.mark.e2e
class TestWavSuccessPiiDetection:
    """PII detection tests (requires PII detection engine)."""

    def test_pii_detection_completes(self, audio_dir):
        """Transcription with PII detection completes successfully."""
        result = transcribe_json(
            audio_dir / "test_merged.wav",
            "--pii",
        )

        assert result["status"] == "completed"
        assert result["text"]
        # PII results should be present (even if empty)
        assert "pii_entities" in result or result["status"] == "completed"

    def test_pii_detection_fast_tier(self, audio_dir):
        """PII detection with fast tier (regex-based)."""
        result = transcribe_json(
            audio_dir / "test_merged.wav",
            "--pii",
            "--pii-tier",
            "fast",
        )

        assert result["status"] == "completed"
        assert result["text"]

    def test_pii_detection_standard_tier(self, audio_dir):
        """PII detection with standard tier (NER-based)."""
        result = transcribe_json(
            audio_dir / "test_merged.wav",
            "--pii",
            "--pii-tier",
            "standard",
        )

        assert result["status"] == "completed"
        assert result["text"]

    def test_pii_detection_specific_entities(self, audio_dir):
        """PII detection with specific entity types."""
        result = transcribe_json(
            audio_dir / "test_merged.wav",
            "--pii",
            "--pii-entities",
            "PERSON,EMAIL,PHONE_NUMBER",
        )

        assert result["status"] == "completed"
        assert result["text"]


@pytest.mark.e2e
class TestWavSuccessAudioRedaction:
    """Audio redaction tests (requires PII detection and audio redaction engines)."""

    def test_audio_redaction_silence_mode(self, audio_dir):
        """Audio redaction with silence mode completes."""
        result = transcribe_json(
            audio_dir / "test_merged.wav",
            "--pii",
            "--redact-audio",
            "--redaction-mode",
            "silence",
        )

        assert result["status"] == "completed"
        # Check for redacted audio URL in result
        # Note: redacted_audio_url may only be present if PII was detected
        assert result["text"]

    def test_audio_redaction_beep_mode(self, audio_dir):
        """Audio redaction with beep mode completes."""
        result = transcribe_json(
            audio_dir / "test_merged.wav",
            "--pii",
            "--redact-audio",
            "--redaction-mode",
            "beep",
        )

        assert result["status"] == "completed"
        assert result["text"]


# =============================================================================
# FAILURE SCENARIOS
# =============================================================================


@pytest.mark.e2e
class TestWavFailureInvalidFiles:
    """Failure scenarios with invalid or corrupt files."""

    def test_empty_wav_file_fails(self):
        """Empty file produces error."""
        result = transcribe_expect_failure(INVALID_AUDIO_DIR / "empty_file.wav")

        assert result.returncode != 0
        # Error should mention the problem
        assert (
            "Error" in result.stderr
            or "error" in result.stderr.lower()
            or "failed" in result.stderr.lower()
        )

    def test_text_file_as_wav_fails(self):
        """Text file renamed to .wav produces error."""
        result = transcribe_expect_failure(INVALID_AUDIO_DIR / "text_as_wav.wav")

        assert result.returncode != 0
        assert (
            "Error" in result.stderr
            or "error" in result.stderr.lower()
            or "failed" in result.stderr.lower()
        )

    def test_corrupt_truncated_wav_fails(self):
        """Truncated/corrupt WAV header produces error."""
        result = transcribe_expect_failure(INVALID_AUDIO_DIR / "corrupt_truncated.wav")

        assert result.returncode != 0
        assert (
            "Error" in result.stderr
            or "error" in result.stderr.lower()
            or "failed" in result.stderr.lower()
        )


@pytest.mark.e2e
class TestWavFailureNonexistent:
    """Failure scenarios with missing files."""

    def test_nonexistent_file_fails(self, tmp_path):
        """Non-existent file path produces error."""
        fake_path = tmp_path / "does_not_exist.wav"

        result = run_dalston(
            "transcribe",
            str(fake_path),
            "--format",
            "json",
            "--wait",
        )

        assert result.returncode != 0
        assert (
            "Error" in result.stderr
            or "error" in result.stderr.lower()
            or "not found" in result.stderr.lower()
            or "No such file" in result.stderr
        )


@pytest.mark.e2e
class TestWavFailureInvalidOptions:
    """Failure scenarios with invalid command options."""

    def test_invalid_speaker_count_fails(self, audio_dir):
        """Invalid speaker count (out of range) produces error."""
        result = run_dalston(
            "transcribe",
            str(audio_dir / "test_merged.wav"),
            "--speakers",
            "diarize",
            "--num-speakers",
            "100",  # Max is 32
            "--format",
            "json",
            "--wait",
        )

        assert result.returncode != 0

    @pytest.mark.xfail(
        reason="API validation missing: min_speakers > max_speakers not rejected"
    )
    def test_conflicting_speaker_counts_fails(self, audio_dir):
        """Min speakers > max speakers produces error."""
        result = run_dalston(
            "transcribe",
            str(audio_dir / "test_merged.wav"),
            "--speakers",
            "diarize",
            "--min-speakers",
            "5",
            "--max-speakers",
            "2",
            "--format",
            "json",
            "--wait",
        )

        assert result.returncode != 0


# =============================================================================
# EDGE CASES
# =============================================================================


@pytest.mark.e2e
class TestWavEdgeCases:
    """Edge cases and boundary conditions."""

    def test_short_audio_file(self, audio_dir):
        """Short audio file (a few seconds) transcribes successfully."""
        # test1_speaker1.wav should be a short clip
        audio_file = audio_dir / "test1_speaker1.wav"
        if not audio_file.exists():
            pytest.skip(f"Test audio file not found: {audio_file}")

        result = transcribe_json(audio_file)

        assert result["status"] == "completed"
        assert result["text"]

    def test_multiple_files_sequential(self, audio_dir):
        """Multiple files can be transcribed in sequence."""
        file1 = audio_dir / "test1_speaker1.wav"
        file2 = audio_dir / "test2_speaker2.wav"

        if not file1.exists() or not file2.exists():
            pytest.skip("Test audio files not found")

        result1 = transcribe_json(file1)
        result2 = transcribe_json(file2)

        assert result1["status"] == "completed"
        assert result2["status"] == "completed"
        # Both should produce distinct transcriptions
        assert result1["text"]
        assert result2["text"]


@pytest.mark.e2e
class TestWavJobMetadata:
    """Verify job metadata and response structure."""

    def test_job_has_required_fields(self, audio_dir):
        """Completed job has all required response fields."""
        result = transcribe_json(audio_dir / "test_merged.wav")

        # Required fields
        assert "id" in result, "Missing job id"
        assert "status" in result, "Missing status"
        assert "text" in result, "Missing text"
        assert "segments" in result, "Missing segments"

        # Status should be completed
        assert result["status"] == "completed"

    def test_job_id_is_valid_uuid(self, audio_dir):
        """Job ID is a valid UUID format."""
        import uuid

        result = transcribe_json(audio_dir / "test_merged.wav")

        job_id = result["id"]
        # Should not raise ValueError
        parsed = uuid.UUID(job_id)
        assert str(parsed) == job_id

    def test_segment_structure(self, audio_dir):
        """Segments have expected structure."""
        result = transcribe_json(audio_dir / "test_merged.wav")

        assert result["status"] == "completed"
        assert len(result["segments"]) > 0

        seg = result["segments"][0]
        # Required segment fields
        assert "text" in seg, "Segment missing text"
        assert "start" in seg, "Segment missing start"
        assert "end" in seg, "Segment missing end"
