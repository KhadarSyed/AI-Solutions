"""Integration tests for PostgresArtifactStore — requires the compose postgres on :5434."""

import uuid

import pytest

from app.artifacts import keys
from app.artifacts.base import ArtifactNotFound
from app.artifacts.postgres_store import PostgresArtifactStore
from app.db.base import get_sessionmaker


@pytest.fixture
def store() -> PostgresArtifactStore:
    return PostgresArtifactStore(get_sessionmaker())


@pytest.fixture
def key() -> str:
    return keys.source_file(uuid.uuid4())


async def test_put_get_json_roundtrip(store, key):
    data = {"articles": [{"id": "A0", "title": "hello"}], "n": 1}
    ref = await store.put_json(key, data)
    assert ref.version == 1
    assert ref.content_type == "application/json"
    assert await store.get_json(key) == data
    await store.delete(key)


async def test_upsert_bumps_version_and_hash(store, key):
    r1 = await store.put_json(key, {"v": 1})
    r2 = await store.put_json(key, {"v": 2})
    assert (r1.version, r2.version) == (1, 2)
    assert r1.sha256 != r2.sha256
    assert await store.get_json(key) == {"v": 2}
    await store.delete(key)


async def test_bytes_roundtrip_and_exists(store):
    key = keys.report(uuid.uuid4(), "out.docx")
    payload = b"\x50\x4b\x03\x04 fake docx"
    ref = await store.put_bytes(key, payload, "application/vnd.openxmlformats")
    assert ref.size_bytes == len(payload)
    assert await store.get_bytes(key) == payload
    assert await store.exists(key)
    await store.delete(key)
    assert not await store.exists(key)


async def test_missing_key_raises(store):
    with pytest.raises(ArtifactNotFound):
        await store.get_json("sessions/nope/missing.json")


def test_key_builders_are_stable():
    sid = "11111111-1111-1111-1111-111111111111"
    assert keys.source_file(sid) == f"sessions/{sid}/source_file.json"
    assert keys.tagged_file(sid) == f"sessions/{sid}/tagged_file.json"
    assert keys.charts_data_file(sid) == f"sessions/{sid}/charts_data_file.json"
    assert keys.gate_csv(sid, 1) == f"sessions/{sid}/gate1_review.csv"
