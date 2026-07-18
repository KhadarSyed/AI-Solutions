from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class ArtifactRef:
    key: str
    content_type: str
    size_bytes: int
    sha256: str
    version: int


class ArtifactNotFound(Exception):
    def __init__(self, key: str):
        super().__init__(f"artifact not found: {key}")
        self.key = key


class ArtifactStore(ABC):
    """Stage-handoff artifact storage with S3-style string keys."""

    @abstractmethod
    async def put_json(self, key: str, data: dict | list) -> ArtifactRef: ...

    @abstractmethod
    async def get_json(self, key: str) -> dict | list: ...

    @abstractmethod
    async def put_bytes(
        self, key: str, data: bytes, content_type: str = "application/octet-stream"
    ) -> ArtifactRef: ...

    @abstractmethod
    async def get_bytes(self, key: str) -> bytes: ...

    @abstractmethod
    async def delete(self, key: str) -> None: ...

    @abstractmethod
    async def exists(self, key: str) -> bool: ...
