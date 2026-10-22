"""S3 (MinIO) client helpers. boto3 is sync, so callers run it via asyncio.to_thread."""

from functools import lru_cache
from typing import Any

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


def all_buckets(settings: Settings) -> list[str]:
    return [settings.s3_bucket_analytics, settings.s3_bucket_news_raw, settings.s3_bucket_snapshots]


def ensure_buckets(client: Any, buckets: list[str]) -> None:
    existing = {b["Name"] for b in client.list_buckets().get("Buckets", [])}
    for bucket in buckets:
        if bucket not in existing:
            client.create_bucket(Bucket=bucket)
