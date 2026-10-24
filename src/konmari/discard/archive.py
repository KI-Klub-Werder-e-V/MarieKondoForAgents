"""Cold-storage targets (filesystem path or S3-compatible bucket) and a stub index for propagation checks."""

import json
import os
from pathlib import Path
from typing import Protocol


class Archive(Protocol):
    def put(self, key: str, data: bytes) -> None: ...
    def get(self, key: str) -> bytes: ...
    def delete(self, key: str) -> None: ...


class FsArchive:
    """A directory readable only by the archive role: the OS user running konmari (0700 dir, 0600 files)."""

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.root.chmod(0o700)

    def put(self, key, data):
        fd = os.open(self.root / key, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(data)

    def get(self, key):
        return (self.root / key).read_bytes()

    def delete(self, key):
        (self.root / key).unlink(missing_ok=True)


class S3Archive:
    """s3://bucket/prefix on any S3-compatible store (MinIO, Ceph, AWS). Endpoint from $KONMARI_S3_ENDPOINT,
    credentials from the standard AWS_* variables; give only the archive role a bucket policy that can read it."""

    def __init__(self, url: str):
        import boto3  # optional extra: pip install konmari[s3]

        self.bucket, _, self.prefix = url.removeprefix("s3://").partition("/")
        self.s3 = boto3.client("s3", endpoint_url=os.environ.get("KONMARI_S3_ENDPOINT"))

    def _key(self, key):
        return f"{self.prefix.rstrip('/')}/{key}" if self.prefix else key

    def put(self, key, data):
        self.s3.put_object(Bucket=self.bucket, Key=self._key(key), Body=data)

    def get(self, key):
        return self.s3.get_object(Bucket=self.bucket, Key=self._key(key))["Body"].read()

    def delete(self, key):
        self.s3.delete_object(Bucket=self.bucket, Key=self._key(key))


def archive_for(target: str | None = None) -> Archive:
    target = target or os.environ.get("KONMARI_ARCHIVE") or ".konmari/archive"
    return S3Archive(target) if target.startswith("s3://") else FsArchive(target)


class JsonIndex:
    """Stub for a downstream index (vector store, search index): a JSON list of artifact IDs still present."""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def _ids(self) -> set[str]:
        return set(json.loads(self.path.read_text(encoding="utf-8"))) if self.path.is_file() else set()

    def present(self, ids) -> set[str]:
        return self._ids() & set(ids)

    def delete(self, ids) -> None:
        self.path.write_text(json.dumps(sorted(self._ids() - set(ids))), encoding="utf-8")
