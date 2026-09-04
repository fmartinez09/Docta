from datetime import UTC, datetime, timedelta
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from docta_api.object_storage import (
    ObjectStorageUnavailable,
    StoredObjectMetadata,
    StoredObjectNotFound,
    StoredObjectTooLarge,
    UploadAuthorization,
)


class S3ObjectStorage:
    def __init__(
        self,
        *,
        endpoint_url: str,
        access_key: str,
        secret_key: str,
        bucket: str,
        region: str,
        upload_ttl_seconds: int,
        timeout_seconds: float,
    ) -> None:
        self._bucket = bucket
        self._region = region
        self._upload_ttl_seconds = upload_ttl_seconds
        self._client: Any = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region,
            config=Config(
                signature_version="s3v4",
                connect_timeout=timeout_seconds,
                read_timeout=timeout_seconds,
                retries={"max_attempts": 2, "mode": "standard"},
                s3={"addressing_style": "path"},
            ),
        )

    def create_upload_authorization(
        self,
        *,
        storage_key: str,
        media_type: str,
        sha256: str,
    ) -> UploadAuthorization:
        self._ensure_bucket()
        headers = {
            "content-type": media_type,
            "x-amz-meta-sha256": sha256,
        }
        try:
            url = self._client.generate_presigned_url(
                "put_object",
                Params={
                    "Bucket": self._bucket,
                    "Key": storage_key,
                    "ContentType": media_type,
                    "Metadata": {"sha256": sha256},
                },
                ExpiresIn=self._upload_ttl_seconds,
                HttpMethod="PUT",
            )
        except (BotoCoreError, ClientError) as error:
            raise ObjectStorageUnavailable from error
        return UploadAuthorization(
            url=url,
            headers=headers,
            expires_at=datetime.now(UTC) + timedelta(seconds=self._upload_ttl_seconds),
        )

    def inspect(self, storage_key: str) -> StoredObjectMetadata:
        try:
            response = self._client.head_object(Bucket=self._bucket, Key=storage_key)
        except ClientError as error:
            status_code = error.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
            if status_code == 404:
                raise StoredObjectNotFound from error
            raise ObjectStorageUnavailable from error
        except BotoCoreError as error:
            raise ObjectStorageUnavailable from error
        return StoredObjectMetadata(
            size_bytes=int(response["ContentLength"]),
            media_type=response.get("ContentType"),
            metadata={str(key): str(value) for key, value in response.get("Metadata", {}).items()},
            version_id=response.get("VersionId"),
        )

    def read(self, storage_key: str, *, version_id: str, max_bytes: int) -> bytes:
        try:
            response = self._client.get_object(
                Bucket=self._bucket,
                Key=storage_key,
                VersionId=version_id,
            )
            body = response["Body"]
            try:
                content = body.read(max_bytes + 1)
            finally:
                body.close()
        except ClientError as error:
            status_code = error.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
            if status_code == 404:
                raise StoredObjectNotFound from error
            raise ObjectStorageUnavailable from error
        except BotoCoreError as error:
            raise ObjectStorageUnavailable from error
        if len(content) > max_bytes:
            raise StoredObjectTooLarge
        return content

    def delete(self, storage_key: str) -> None:
        try:
            versions = self._client.list_object_versions(Bucket=self._bucket, Prefix=storage_key)
            objects = [
                {"Key": item["Key"], "VersionId": item["VersionId"]}
                for group in ("Versions", "DeleteMarkers")
                for item in versions.get(group, [])
                if item["Key"] == storage_key
            ]
            if objects:
                self._client.delete_objects(
                    Bucket=self._bucket,
                    Delete={"Objects": objects, "Quiet": True},
                )
        except (BotoCoreError, ClientError) as error:
            raise ObjectStorageUnavailable from error

    def _ensure_bucket(self) -> None:
        exists = False
        try:
            self._client.head_bucket(Bucket=self._bucket)
            exists = True
        except ClientError as error:
            status_code = error.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
            if status_code not in (400, 404):
                raise ObjectStorageUnavailable from error
        except BotoCoreError as error:
            raise ObjectStorageUnavailable from error

        if not exists:
            parameters: dict[str, object] = {"Bucket": self._bucket}
            if self._region != "us-east-1":
                parameters["CreateBucketConfiguration"] = {"LocationConstraint": self._region}
            try:
                self._client.create_bucket(**parameters)
            except ClientError as error:
                code = error.response.get("Error", {}).get("Code")
                if code not in ("BucketAlreadyExists", "BucketAlreadyOwnedByYou"):
                    raise ObjectStorageUnavailable from error
            except BotoCoreError as error:
                raise ObjectStorageUnavailable from error
        try:
            self._client.put_bucket_versioning(
                Bucket=self._bucket,
                VersioningConfiguration={"Status": "Enabled"},
            )
        except (BotoCoreError, ClientError) as error:
            raise ObjectStorageUnavailable from error
