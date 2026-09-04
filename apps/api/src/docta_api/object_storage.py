from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


class ObjectStorageUnavailable(Exception):
    pass


class StoredObjectNotFound(Exception):
    pass


class StoredObjectTooLarge(Exception):
    pass


@dataclass(frozen=True)
class UploadAuthorization:
    url: str
    headers: dict[str, str]
    expires_at: datetime


@dataclass(frozen=True)
class StoredObjectMetadata:
    size_bytes: int
    media_type: str | None
    metadata: dict[str, str]
    version_id: str | None


class ObjectStorage(Protocol):
    def create_upload_authorization(
        self,
        *,
        storage_key: str,
        media_type: str,
        sha256: str,
    ) -> UploadAuthorization: ...

    def inspect(self, storage_key: str) -> StoredObjectMetadata: ...

    def read(self, storage_key: str, *, version_id: str, max_bytes: int) -> bytes: ...


class UnconfiguredObjectStorage:
    def create_upload_authorization(
        self,
        *,
        storage_key: str,
        media_type: str,
        sha256: str,
    ) -> UploadAuthorization:
        raise ObjectStorageUnavailable

    def inspect(self, storage_key: str) -> StoredObjectMetadata:
        raise ObjectStorageUnavailable

    def read(self, storage_key: str, *, version_id: str, max_bytes: int) -> bytes:
        raise ObjectStorageUnavailable
