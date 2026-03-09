import pytest

from dalston.gateway.services.artifact_store import (
    InMemoryArtifactStoreAdapter,
    LocalFilesystemArtifactStoreAdapter,
)


@pytest.mark.asyncio
async def test_local_fs_artifact_store_roundtrip(tmp_path) -> None:
    store = LocalFilesystemArtifactStoreAdapter(str(tmp_path))
    uri = await store.write_bytes("jobs/a/test.bin", b"hello")
    payload = await store.read_bytes(uri)
    assert payload == b"hello"
    assert await store.exists(uri)


@pytest.mark.asyncio
async def test_local_fs_artifact_store_rejects_key_path_traversal(tmp_path) -> None:
    store = LocalFilesystemArtifactStoreAdapter(str(tmp_path))

    with pytest.raises(ValueError, match="escapes artifact root"):
        await store.write_bytes("../escape.bin", b"bad")


@pytest.mark.asyncio
async def test_local_fs_artifact_store_rejects_uri_outside_root(tmp_path) -> None:
    store = LocalFilesystemArtifactStoreAdapter(str(tmp_path))
    outside_file = tmp_path.parent / "outside.bin"
    outside_file.write_bytes(b"bad")

    with pytest.raises(ValueError, match="escapes artifact root"):
        await store.read_bytes(f"file://{outside_file.resolve()}")


@pytest.mark.asyncio
async def test_local_fs_artifact_store_prefix_helpers(tmp_path) -> None:
    store = LocalFilesystemArtifactStoreAdapter(str(tmp_path))

    assert not await store.has_prefix("jobs/abc/audio/")

    await store.write_bytes("jobs/abc/audio/original.wav", b"audio")
    await store.write_bytes("jobs/abc/tasks/t1/output.json", b"{}")
    assert await store.has_prefix("jobs/abc/audio/")

    await store.delete_prefix("jobs/abc/audio/")
    assert not await store.has_prefix("jobs/abc/audio/")
    assert await store.has_prefix("jobs/abc/tasks/")


def test_local_fs_artifact_store_is_lazy(tmp_path) -> None:
    root = tmp_path / "artifacts"
    assert not root.exists()

    LocalFilesystemArtifactStoreAdapter(str(root))

    assert not root.exists()


@pytest.mark.asyncio
async def test_in_memory_artifact_store_roundtrip() -> None:
    store = InMemoryArtifactStoreAdapter()
    uri = await store.write_bytes("jobs/a/test.bin", b"hello")

    assert uri == "memory://jobs/a/test.bin"
    assert await store.exists(uri)
    assert await store.read_bytes(uri) == b"hello"


@pytest.mark.asyncio
async def test_in_memory_artifact_store_delete_prefix() -> None:
    store = InMemoryArtifactStoreAdapter()
    await store.write_bytes("jobs/abc/audio/original.wav", b"audio")
    await store.write_bytes("jobs/abc/tasks/t1/output.json", b"{}")

    assert await store.has_prefix("jobs/abc/audio/")
    assert await store.has_prefix("jobs/abc/tasks/")

    await store.delete_prefix("jobs/abc/audio/")

    assert not await store.has_prefix("jobs/abc/audio/")
    assert await store.has_prefix("jobs/abc/tasks/")


@pytest.mark.asyncio
async def test_in_memory_artifact_store_missing_key_raises() -> None:
    store = InMemoryArtifactStoreAdapter()

    with pytest.raises(FileNotFoundError):
        await store.read_bytes("memory://jobs/missing/transcript.json")
