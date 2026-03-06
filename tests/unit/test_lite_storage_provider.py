import pytest

from dalston.gateway.services.artifact_store import LocalFilesystemArtifactStoreAdapter


@pytest.mark.asyncio
async def test_local_fs_artifact_store_roundtrip(tmp_path) -> None:
    store = LocalFilesystemArtifactStoreAdapter(str(tmp_path))
    uri = await store.write_bytes("jobs/a/test.bin", b"hello")
    payload = await store.read_bytes(uri)
    assert payload == b"hello"


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
