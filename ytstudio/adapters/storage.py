"""Storage (Strategy): local disk or any S3-compatible store (MinIO in docker-compose)."""

import os
import shutil
import tempfile
from pathlib import Path

from ytstudio.domain.errors import PermanentError


def _safe_key(key: str) -> str:
    if key.startswith("/") or ".." in Path(key).parts:
        raise PermanentError(f"bad storage key {key!r}")
    return key


class LocalStorage:
    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)

    def put(self, local: Path, key: str) -> str:
        dest = self.root / _safe_key(key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        part = dest.with_name(f".{dest.name}.part")
        shutil.copyfile(local, part)
        os.replace(part, dest)  # atomic on one filesystem
        return key

    def get(self, key: str) -> Path:
        path = self.root / _safe_key(key)
        if not path.exists():
            raise PermanentError(f"missing file {key}")
        return path

    def delete(self, key: str) -> None:
        (self.root / _safe_key(key)).unlink(missing_ok=True)

    def exists(self, key: str) -> bool:
        return (self.root / _safe_key(key)).exists()


class S3Storage:
    def __init__(self, endpoint: str, bucket: str, access_key: str, secret_key: str, cache_dir: Path):
        import boto3

        self.s3 = boto3.client(
            "s3",
            endpoint_url=endpoint or None,
            aws_access_key_id=access_key or None,
            aws_secret_access_key=secret_key or None,
        )
        self.bucket, self.cache = bucket, cache_dir
        cache_dir.mkdir(parents=True, exist_ok=True)
        try:
            self.s3.head_bucket(Bucket=bucket)
        except Exception:
            self.s3.create_bucket(Bucket=bucket)

    def put(self, local: Path, key: str) -> str:
        self.s3.upload_file(str(local), self.bucket, _safe_key(key))  # an object appears whole or not at all
        return key

    def get(self, key: str) -> Path:
        path = self.cache / _safe_key(key)
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=path.parent)
            os.close(fd)
            self.s3.download_file(self.bucket, key, tmp)
            os.replace(tmp, path)
        return path

    def delete(self, key: str) -> None:
        self.s3.delete_object(Bucket=self.bucket, Key=_safe_key(key))
        (self.cache / key).unlink(missing_ok=True)

    def exists(self, key: str) -> bool:
        try:
            self.s3.head_object(Bucket=self.bucket, Key=_safe_key(key))
            return True
        except Exception:
            return False
