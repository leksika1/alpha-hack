"""S3 (MinIO) client helpers. boto3 is sync, so callers run it via asyncio.to_thread."""

from functools import lru_cache
from typing import Any, Protocol

import boto3
from botocore.config import Config

from app.core.config import Settings, get_settings


def make_s3_client(settings: Settings, public: bool = False) -> Any:
    endpoint = settings.s3_public_endpoint_url if public and settings.s3_public_endpoint_url else None
    return boto3.client(
        "s3",
        endpoint_url=endpoint or settings.s3_endpoint_url,
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key.get_secret_value(),
        region_name=settings.s3_region,
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}, retries={"max_attempts": 3}),
    )


@lru_cache
def get_s3_client() -> Any:
    return make_s3_client(get_settings())


@lru_cache
def get_public_s3_client() -> Any:
    """Signs URLs with the host the browser can reach (S3_PUBLIC_ENDPOINT_URL)."""
    return make_s3_client(get_settings(), public=True)


class ObjectStore(Protocol):
    def put(self, key: str, body: bytes, content_type: str | None) -> None: ...

    def get(self, key: str) -> bytes: ...


class S3ObjectStore:
    """boto3 adapter for one bucket (callers run it via asyncio.to_thread)."""

    def __init__(self, client: Any, bucket: str) -> None:
        self.client = client
        self.bucket = bucket

    def put(self, key: str, body: bytes, content_type: str | None) -> None:
        self.client.put_object(
            Bucket=self.bucket, Key=key, Body=body, ContentType=content_type or "application/octet-stream"
        )

    def get(self, key: str) -> bytes:
        data: bytes = self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read()
        return data

    def exists(self, key: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
            return True
        except self.client.exceptions.ClientError:
            return False


def presigned_get(client: Any, bucket: str, key: str, expires_s: int, filename: str | None = None) -> str:
    params: dict[str, Any] = {"Bucket": bucket, "Key": key}
    if filename:
        params["ResponseContentDisposition"] = f'inline; filename="{filename}"'
        params["ResponseContentType"] = "application/pdf"
    url: str = client.generate_presigned_url("get_object", Params=params, ExpiresIn=expires_s)
    return url


def all_buckets(settings: Settings) -> list[str]:
    return [settings.s3_bucket_analytics, settings.s3_bucket_news_raw, settings.s3_bucket_snapshots]


def ensure_buckets(client: Any, buckets: list[str]) -> None:
    existing = {b["Name"] for b in client.list_buckets().get("Buckets", [])}
    for bucket in buckets:
        if bucket not in existing:
            client.create_bucket(Bucket=bucket)
