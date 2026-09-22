import os
import re
from uuid import uuid4

import boto3
import psycopg
import pytest
from psycopg import sql
from redis import Redis

from docta_api.config import get_settings


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(scope="session")
def integration_resources():
    """Only the dedicated Compose test services; never derive destructive targets from .env."""
    storage_port = int(os.environ.get("DOCTA_TEST_MINIO_PORT", "59000"))
    assert 1024 <= storage_port <= 65535
    storage_endpoint = f"http://127.0.0.1:{storage_port}"
    suffix = uuid4().hex
    database = f"docta_test_{suffix}"
    bucket = f"docta-test-{suffix}"
    stream = f"docta:test:{suffix}:ingestion"
    admin_url = "postgresql://docta_test:docta_test_only@127.0.0.1:55432/postgres"
    with psycopg.connect(admin_url, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
    values = {
        "DOCTA_ENVIRONMENT": "test",
        "DOCTA_DATABASE_URL": admin_url.removesuffix("postgres") + database,
        "DOCTA_MINIO_HEALTH_URL": f"{storage_endpoint}/minio/health/ready",
        "DOCTA_S3_ENDPOINT_URL": storage_endpoint,
        "DOCTA_S3_ACCESS_KEY": "docta-test",
        "DOCTA_S3_SECRET_KEY": "docta-test-secret",
        "DOCTA_S3_BUCKET": bucket,
        "DOCTA_REDIS_URL": "redis://127.0.0.1:56379/0",
        "DOCTA_REDIS_STREAM": stream,
    }
    try:
        yield values
    finally:
        # Generated, isolated targets only; no FLUSHDB and no development schema downgrade.
        assert re.fullmatch(r"docta_test_[0-9a-f]{32}", database)
        with psycopg.connect(admin_url, autocommit=True) as connection:
            connection.execute(
                sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(database))
            )
        with Redis.from_url(values["DOCTA_REDIS_URL"]) as client:
            client.delete(stream, f"{stream}:dead")
        storage = boto3.client(
            "s3",
            endpoint_url=values["DOCTA_S3_ENDPOINT_URL"],
            aws_access_key_id=values["DOCTA_S3_ACCESS_KEY"],
            aws_secret_access_key=values["DOCTA_S3_SECRET_KEY"],
        )
        if bucket in {entry["Name"] for entry in storage.list_buckets()["Buckets"]}:
            paginator = storage.get_paginator("list_object_versions")
            for page in paginator.paginate(Bucket=bucket):
                objects = [
                    {"Key": obj["Key"], "VersionId": obj["VersionId"]}
                    for obj in page.get("Versions", []) + page.get("DeleteMarkers", [])
                ]
                if objects:
                    storage.delete_objects(Bucket=bucket, Delete={"Objects": objects})
            storage.delete_bucket(Bucket=bucket)
        storage.close()


@pytest.fixture(autouse=True)
def isolated_integration(request, monkeypatch):
    if request.node.get_closest_marker("integration"):
        resources = request.getfixturevalue("integration_resources")
        for key, value in resources.items():
            monkeypatch.setenv(key, value)
        stream = f'{resources["DOCTA_REDIS_STREAM"]}:{uuid4().hex}'
        monkeypatch.setenv("DOCTA_REDIS_STREAM", stream)
        get_settings.cache_clear()
        try:
            yield
        finally:
            get_settings.cache_clear()
            with Redis.from_url(resources["DOCTA_REDIS_URL"]) as client:
                client.delete(stream, f"{stream}:dead")
    else:
        yield


@pytest.fixture
def ingestion_runtime(isolated_integration):
    from docta_api.config import Settings
    from docta_api.worker import WorkerRuntime

    runtime = WorkerRuntime(Settings())
    try:
        yield runtime
    finally:
        runtime.close()
