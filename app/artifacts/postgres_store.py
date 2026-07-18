import hashlib
import json

from sqlalchemy import delete as sa_delete
from sqlalchemy import null, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.artifacts.base import ArtifactNotFound, ArtifactRef, ArtifactStore
from app.db.models import Artifact


class PostgresArtifactStore(ArtifactStore):
    """Artifacts as JSONB/BYTEA rows, upserted by key with version bumps."""

    def __init__(self, sessionmaker: async_sessionmaker):
        self._sm = sessionmaker

    async def put_json(self, key: str, data: dict | list) -> ArtifactRef:
        raw = json.dumps(data, ensure_ascii=False).encode()
        return await self._put(key, "application/json", json_data=data, raw=raw)

    async def put_bytes(
        self, key: str, data: bytes, content_type: str = "application/octet-stream"
    ) -> ArtifactRef:
        return await self._put(key, content_type, binary_data=data, raw=data)

    async def _put(
        self,
        key: str,
        content_type: str,
        raw: bytes,
        json_data: dict | list | None = None,
        binary_data: bytes | None = None,
    ) -> ArtifactRef:
        digest = hashlib.sha256(raw).hexdigest()
        # None must become SQL NULL, not JSON 'null' — the CHECK constraint depends on it.
        json_value = json_data if json_data is not None else null()
        stmt = pg_insert(Artifact).values(
            key=key,
            content_type=content_type,
            json_data=json_value,
            binary_data=binary_data,
            size_bytes=len(raw),
            sha256=digest,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[Artifact.key],
            set_={
                "content_type": content_type,
                "json_data": json_value,
                "binary_data": binary_data,
                "size_bytes": len(raw),
                "sha256": digest,
                "version": Artifact.version + 1,
            },
        ).returning(Artifact.version)
        async with self._sm() as session, session.begin():
            version = (await session.execute(stmt)).scalar_one()
        return ArtifactRef(key, content_type, len(raw), digest, version)

    async def get_json(self, key: str) -> dict | list:
        async with self._sm() as session:
            row = (
                await session.execute(select(Artifact.json_data).where(Artifact.key == key))
            ).one_or_none()
        if row is None or row[0] is None:
            raise ArtifactNotFound(key)
        return row[0]

    async def get_bytes(self, key: str) -> bytes:
        async with self._sm() as session:
            row = (
                await session.execute(
                    select(Artifact.binary_data, Artifact.json_data).where(Artifact.key == key)
                )
            ).one_or_none()
        if row is None:
            raise ArtifactNotFound(key)
        if row[0] is not None:
            return row[0]
        return json.dumps(row[1], ensure_ascii=False).encode()

    async def delete(self, key: str) -> None:
        async with self._sm() as session, session.begin():
            await session.execute(sa_delete(Artifact).where(Artifact.key == key))

    async def exists(self, key: str) -> bool:
        async with self._sm() as session:
            return (
                await session.execute(select(Artifact.id).where(Artifact.key == key))
            ).one_or_none() is not None
