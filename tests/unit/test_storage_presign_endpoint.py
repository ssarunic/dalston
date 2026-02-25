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
