"""Artifact storage backends for engine_id modes."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Protocol

from botocore.exceptions import ClientError

from dalston.common.s3 import get_s3_client
from dalston.config import Settings


class ArtifactStore(Protocol):
    async def uri_for_key(self, key: str) -> str: ...

    async def write_bytes(
        self, key: str, payload: bytes, content_type: str | None = None
    ) -> str: ...

    async def read_bytes(self, uri: str) -> bytes: ...

    async def exists(self, uri: str) -> bool: ...

    async def has_prefix(self, prefix: str) -> bool: ...

    async def delete_prefix(self, prefix: str) -> None: ...


class S3ArtifactStoreAdapter:
    def __init__(self, settings: Settings):
        self._settings = settings
        self._bucket = settings.s3_bucket

    @staticmethod
    def _parse_s3_uri(uri: str) -> tuple[str, str]:
        if not uri.startswith("s3://"):
            raise ValueError("Unsupported S3 artifact URI")
        _, _, rest = uri.partition("s3://")
        bucket, _, key = rest.partition("/")
        if not bucket or not key:
            raise ValueError("Malformed S3 artifact URI")
        return bucket, key

    async def uri_for_key(self, key: str) -> str:
        return f"s3://{self._bucket}/{key}"

    async def write_bytes(
        self, key: str, payload: bytes, content_type: str | None = None
    ) -> str:
        async with get_s3_client(self._settings) as s3:
            kwargs = {"Bucket": self._bucket, "Key": key, "Body": payload}
            if content_type:
                kwargs["ContentType"] = content_type
            await s3.put_object(**kwargs)
        return await self.uri_for_key(key)

    async def read_bytes(self, uri: str) -> bytes:
        bucket, key = self._parse_s3_uri(uri)
        async with get_s3_client(self._settings) as s3:
            try:
                obj = await s3.get_object(Bucket=bucket, Key=key)
                return await obj["Body"].read()
            except ClientError as exc:
                if exc.response["Error"]["Code"] in ("404", "NoSuchKey"):
                    raise FileNotFoundError(uri) from exc
                raise

    async def exists(self, uri: str) -> bool:
        bucket, key = self._parse_s3_uri(uri)
        async with get_s3_client(self._settings) as s3:
            try:
                await s3.head_object(Bucket=bucket, Key=key)
                return True
            except ClientError as exc:
                if exc.response["Error"]["Code"] in ("404", "NoSuchKey"):
                    return False
                raise

    async def has_prefix(self, prefix: str) -> bool:
        async with get_s3_client(self._settings) as s3:
            response = await s3.list_objects_v2(
                Bucket=self._bucket,
                Prefix=prefix,
                MaxKeys=1,
            )
            return response.get("KeyCount", 0) > 0

    async def delete_prefix(self, prefix: str) -> None:
        async with get_s3_client(self._settings) as s3:
            paginator = s3.get_paginator("list_objects_v2")
            async for page in paginator.paginate(Bucket=self._bucket, Prefix=prefix):
                if "Contents" not in page:
                    continue

                objects = [{"Key": obj["Key"]} for obj in page["Contents"]]
                if objects:
                    await s3.delete_objects(
                        Bucket=self._bucket,
                        Delete={"Objects": objects},
                    )


class LocalFilesystemArtifactStoreAdapter:
    """Lite adapter backed by local filesystem root."""

    def __init__(self, root_dir: str):
        self._root = Path(root_dir).expanduser().resolve()

    def _ensure_within_root(self, path: Path, source: str) -> Path:
        resolved = path.expanduser().resolve()
        try:
            resolved.relative_to(self._root)
        except ValueError as exc:
            raise ValueError(f"{source} escapes artifact root: {path!s}") from exc
        return resolved

    def _path_for_key(self, key: str) -> Path:
        return self._ensure_within_root(self._root / key, "Artifact key")

    def _path_for_uri(self, uri: str) -> Path:
        if not uri.startswith("file://"):
            raise ValueError("Unsupported local artifact URI")
        return self._ensure_within_root(
            Path(uri.removeprefix("file://")), "Artifact URI"
        )

    async def uri_for_key(self, key: str) -> str:
        return f"file://{self._path_for_key(key)}"

    async def write_bytes(
        self, key: str, payload: bytes, content_type: str | None = None
    ) -> str:
        path = self._path_for_key(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        return await self.uri_for_key(key)

    async def read_bytes(self, uri: str) -> bytes:
        return self._path_for_uri(uri).read_bytes()

    async def exists(self, uri: str) -> bool:
        return self._path_for_uri(uri).exists()

    async def has_prefix(self, prefix: str) -> bool:
        path = self._path_for_key(prefix.rstrip("/"))
        if not path.exists():
            return False
        if path.is_file():
            return True
        return any(path.iterdir())

    async def delete_prefix(self, prefix: str) -> None:
        path = self._path_for_key(prefix.rstrip("/"))
        if path.is_file():
            path.unlink(missing_ok=True)
        elif path.is_dir():
            shutil.rmtree(path, ignore_errors=True)

    async def write_json(self, key: str, payload: dict) -> str:
        return await self.write_bytes(
            key, json.dumps(payload).encode("utf-8"), "application/json"
        )


class InMemoryArtifactStoreAdapter:
    """Ephemeral adapter backed by in-memory bytes.

    Intended for request-scoped lite mode flows where artifacts should never
    touch disk. The store can be discarded after request completion.
    """

    def __init__(self):
        self._objects: dict[str, bytes] = {}

    @staticmethod
    def _normalize_key(key: str) -> str:
        normalized = key.lstrip("/")
        if not normalized:
            raise ValueError("Artifact key cannot be empty")
        return normalized

    @classmethod
    def _key_for_uri(cls, uri: str) -> str:
        if not uri.startswith("memory://"):
            raise ValueError("Unsupported in-memory artifact URI")
        key = uri.removeprefix("memory://").lstrip("/")
        return cls._normalize_key(key)

    @classmethod
    def _prefix_matches(cls, key: str, prefix: str) -> bool:
        clean_prefix = prefix.rstrip("/")
        if not clean_prefix:
            return True
        return key == clean_prefix or key.startswith(f"{clean_prefix}/")

    async def uri_for_key(self, key: str) -> str:
        return f"memory://{self._normalize_key(key)}"

    async def write_bytes(
        self, key: str, payload: bytes, content_type: str | None = None
    ) -> str:
        del content_type
        normalized = self._normalize_key(key)
        self._objects[normalized] = bytes(payload)
        return await self.uri_for_key(normalized)

    async def read_bytes(self, uri: str) -> bytes:
        key = self._key_for_uri(uri)
        if key not in self._objects:
            raise FileNotFoundError(uri)
        return self._objects[key]

    async def exists(self, uri: str) -> bool:
        key = self._key_for_uri(uri)
        return key in self._objects

    async def has_prefix(self, prefix: str) -> bool:
        normalized_prefix = prefix.lstrip("/")
        return any(
            self._prefix_matches(key, normalized_prefix) for key in self._objects
        )

    async def delete_prefix(self, prefix: str) -> None:
        normalized_prefix = prefix.lstrip("/")
        keys = [
            key for key in self._objects if self._prefix_matches(key, normalized_prefix)
        ]
        for key in keys:
            self._objects.pop(key, None)


def build_artifact_store(settings: Settings) -> ArtifactStore:
    if settings.runtime_mode == "lite":
        return LocalFilesystemArtifactStoreAdapter(settings.lite_artifacts_dir)
    return S3ArtifactStoreAdapter(settings)
