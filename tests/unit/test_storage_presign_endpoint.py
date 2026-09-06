from urllib.parse import parse_qs, urlsplit

import pytest

from dalston.config import Settings
from dalston.gateway.services.storage import StorageService


def make_settings(**overrides) -> Settings:
    """Create Settings with explicit values, ignoring .env file."""
    base = {
        "DALSTON_S3_BUCKET": "dalston-artifacts",
        "DALSTON_S3_REGION": "us-east-1",
    }
    base.update(overrides)
    # Disable env file loading to isolate test from .env
    return Settings(**base, _env_file=None)


def test_presign_endpoint_uses_explicit_public_endpoint() -> None:
    settings = make_settings(
        DALSTON_S3_ENDPOINT_URL="http://minio:9000",
        DALSTON_S3_PUBLIC_ENDPOINT_URL="https://storage.example.com",
    )
    storage = StorageService(settings)

    assert storage.resolve_presign_endpoint() == "https://storage.example.com"


def test_presign_endpoint_falls_back_to_localhost_for_minio() -> None:
    settings = make_settings(DALSTON_S3_ENDPOINT_URL="http://minio:9000")
    storage = StorageService(settings)

    assert storage.resolve_presign_endpoint() == "http://localhost:9000"


def test_presign_endpoint_keeps_default_for_non_minio() -> None:
    settings = make_settings(
        DALSTON_S3_ENDPOINT_URL="https://s3.us-east-1.amazonaws.com"
    )
    storage = StorageService(settings)

    assert storage.resolve_presign_endpoint() is None


def test_attachment_disposition_keeps_key_extension() -> None:
    value = StorageService.attachment_disposition(
        "sess_0af7bc41f8654865", "realtime/sess_0af7bc41f8654865/audio.WAV"
    )
    assert value == 'attachment; filename="sess_0af7bc41f8654865.wav"'


def test_attachment_disposition_sanitizes_stem_and_drops_odd_extension() -> None:
    value = StorageService.attachment_disposition(
        'a"b/c d', "jobs/x/audio.tar.gz-backup"
    )
    assert value == 'attachment; filename="a_b_c_d"'
    assert StorageService.attachment_disposition("   ", "jobs/x/audio.mp3") == (
        'attachment; filename="audio.mp3"'
    )


@pytest.mark.asyncio
async def test_presigned_url_signs_content_disposition() -> None:
    settings = make_settings(
        DALSTON_S3_ENDPOINT_URL="http://minio:9000",
        AWS_ACCESS_KEY_ID="test",
        AWS_SECRET_ACCESS_KEY="test",
    )
    storage = StorageService(settings)

    inline = await storage.generate_presigned_url("jobs/j1/audio.wav")
    attachment = await storage.generate_presigned_url(
        "jobs/j1/audio.wav",
        content_disposition=StorageService.attachment_disposition("j1", "audio.wav"),
    )

    inline_q = parse_qs(urlsplit(inline).query)
    attachment_q = parse_qs(urlsplit(attachment).query)
    assert "response-content-disposition" not in inline_q
    assert attachment_q["response-content-disposition"] == [
        'attachment; filename="j1.wav"'
    ]
