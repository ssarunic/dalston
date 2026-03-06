import pytest

from dalston.gateway.services.artifact_store import LocalFilesystemArtifactStoreAdapter


@pytest.mark.asyncio
async def test_local_fs_artifact_store_roundtrip(tmp_path) -> None:
    store = LocalFilesystemArtifactStoreAdapter(str(tmp_path))
    uri = await store.write_bytes("jobs/a/test.bin", b"hello")
    payload = await store.read_bytes(uri)
    assert payload == b"hello"
