from functools import lru_cache

from app.artifacts.base import ArtifactStore
from app.config.settings import get_settings


@lru_cache
def get_artifact_store() -> ArtifactStore:
    settings = get_settings()
    if settings.artifact_backend == "postgres":
        from app.artifacts.postgres_store import PostgresArtifactStore
        from app.db.base import get_sessionmaker

        return PostgresArtifactStore(get_sessionmaker())
    if settings.artifact_backend == "s3":
        raise NotImplementedError(
            "S3 artifact backend is a planned drop-in: implement S3ArtifactStore against "
            "app.artifacts.base.ArtifactStore and register it here. Keys are already S3-style."
        )
    raise ValueError(f"unknown ARTIFACT_BACKEND: {settings.artifact_backend}")
