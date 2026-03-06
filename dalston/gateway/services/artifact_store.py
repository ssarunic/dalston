"""Artifact storage backends for runtime modes."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol

from dalston.common.s3 import get_s3_client
from dalston.config import Settings


class ArtifactStore(Protocol):
    async def write_bytes(
        self, key: str, payload: bytes, content_type: str | None = None
    ) -> str: ...

    async def read_bytes(self, uri: str) -> bytes: ...


class S3ArtifactStoreAdapter:
    def __init__(self, settings: Settings):
        self._settings = settings
        self._bucket = settings.s3_bucket

    async def write_bytes(
        self, key: str, payload: bytes, content_type: str | None = None
    ) -> str:
        async with get_s3_client(self._settings) as s3:
            kwargs = {"Bucket": self._bucket, "Key": key, "Body": payload}
            if content_type:
                kwargs["ContentType"] = content_type
            await s3.put_object(**kwargs)
        return f"s3://{self._bucket}/{key}"

    async def read_bytes(self, uri: str) -> bytes:
        _, _, rest = uri.partition("s3://")
        bucket, _, key = rest.partition("/")
        async with get_s3_client(self._settings) as s3:
            obj = await s3.get_object(Bucket=bucket, Key=key)
            return await obj["Body"].read()


class LocalFilesystemArtifactStoreAdapter:
    """Lite adapter backed by local filesystem root."""

    def __init__(self, root_dir: str):
        self._root = Path(root_dir).expanduser().resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    def _path_for_key(self, key: str) -> Path:
        return (self._root / key).resolve()

    def _path_for_uri(self, uri: str) -> Path:
        if not uri.startswith("file://"):
            raise ValueError("Unsupported local artifact URI")
        return Path(uri.removeprefix("file://"))

    async def write_bytes(
        self, key: str, payload: bytes, content_type: str | None = None
    ) -> str:
        path = self._path_for_key(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        return f"file://{path}"

    async def read_bytes(self, uri: str) -> bytes:
        return self._path_for_uri(uri).read_bytes()

    async def write_json(self, key: str, payload: dict) -> str:
        return await self.write_bytes(
            key, json.dumps(payload).encode("utf-8"), "application/json"
        )


def build_artifact_store(settings: Settings) -> ArtifactStore:
    if settings.runtime_mode == "lite":
        return LocalFilesystemArtifactStoreAdapter(settings.lite_artifacts_dir)
    return S3ArtifactStoreAdapter(settings)
