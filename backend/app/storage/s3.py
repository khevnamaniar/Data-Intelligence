"""S3-compatible private cloud storage (production).

Works with Neon Object Storage (recommended — same account as the database), Cloudflare R2,
Backblaze B2, Supabase Storage (S3 endpoint) and AWS S3. Uses the official ``boto3`` library.

Settings (either naming style is accepted, so you can paste Neon's "Connect → Storage → .env"
block into Render unchanged):

    S3_BUCKET              bucket name (required)
    S3_ENDPOINT_URL        or AWS_ENDPOINT_URL_S3   storage address
    S3_ACCESS_KEY_ID       or AWS_ACCESS_KEY_ID     access key id
    S3_SECRET_ACCESS_KEY   or AWS_SECRET_ACCESS_KEY secret
    S3_REGION              or AWS_REGION            region (e.g. ap-southeast-1, or 'auto' for R2)

Objects are always private: the app never creates public links; files are only served through
the authenticated, authorised API.
"""
from __future__ import annotations

import logging
import os

from .base import StorageError, StorageProvider, validate_key
from . import StorageQuotaError

log = logging.getLogger("dip.storage")


def _env(*names: str, default: str = "") -> str:
    for n in names:
        v = os.getenv(n)
        if v:
            return v.strip()
    return default


QUOTA_CODES = {"QuotaExceeded", "StorageQuotaExceeded", "InsufficientStorage", "EntityTooLarge", "ServiceQuotaExceeded"}


class S3StorageProvider(StorageProvider):
    name = "s3"

    def __init__(self, bucket: str, endpoint_url: str | None, access_key: str, secret_key: str, region: str = "", client=None):
        self.bucket = bucket
        self.endpoint_url = endpoint_url or None
        if client is not None:  # tests inject a fake client
            self.client = client
            return
        try:
            import boto3
            from botocore.config import Config
        except ImportError as exc:  # pragma: no cover
            raise StorageError("Cloud storage needs the boto3 package: pip install -r requirements.txt") from exc
        cfg = dict(
            signature_version="s3v4",
            s3={"addressing_style": "path"},  # Neon Object Storage requires path-style; works for R2/B2/S3 too
            retries={"max_attempts": 3, "mode": "standard"},
            connect_timeout=10,
            read_timeout=60,
        )
        try:  # newer botocore: only send checksums when required (better S3-compatible support)
            cfg_obj = Config(**cfg, request_checksum_calculation="when_required", response_checksum_validation="when_required")
        except TypeError:  # pragma: no cover - older botocore
            cfg_obj = Config(**cfg)
        self.client = boto3.client(
            "s3", endpoint_url=self.endpoint_url, aws_access_key_id=access_key, aws_secret_access_key=secret_key,
            region_name=region or "auto", config=cfg_obj)

    @classmethod
    def from_env(cls) -> "S3StorageProvider":
        bucket = _env("S3_BUCKET")
        key_id = _env("S3_ACCESS_KEY_ID", "AWS_ACCESS_KEY_ID")
        secret = _env("S3_SECRET_ACCESS_KEY", "AWS_SECRET_ACCESS_KEY")
        missing = [n for n, v in (("S3_BUCKET", bucket), ("AWS_ACCESS_KEY_ID", key_id), ("AWS_SECRET_ACCESS_KEY", secret)) if not v]
        if missing:
            raise StorageError("Cloud storage is selected (STORAGE_PROVIDER=s3) but these settings are missing: " + ", ".join(missing))
        return cls(bucket=bucket, endpoint_url=_env("S3_ENDPOINT_URL", "AWS_ENDPOINT_URL_S3", "AWS_ENDPOINT_URL") or None,
                   access_key=key_id, secret_key=secret, region=_env("S3_REGION", "AWS_REGION", "AWS_DEFAULT_REGION"))

    # ------------------------------------------------------------- helpers
    @staticmethod
    def _code(exc: Exception) -> str:
        resp = getattr(exc, "response", None) or {}
        return str((resp.get("Error") or {}).get("Code", ""))

    def _fail(self, action: str, key: str, exc: Exception):
        code = self._code(exc)
        log.warning("Storage %s failed for an object (code=%s, type=%s)", action, code or "?", exc.__class__.__name__)
        if code in QUOTA_CODES or "quota" in str(exc).lower() or "limit" in code.lower():
            raise StorageQuotaError("The file storage limit has been reached.") from exc
        if code in ("NoSuchKey", "404", "NotFound"):
            raise StorageError(f"Stored object not found: {key}") from exc
        if code in ("NoSuchBucket",):
            raise StorageError("The storage bucket does not exist. Check the S3_BUCKET setting.") from exc
        if code in ("InvalidAccessKeyId", "SignatureDoesNotMatch", "AccessDenied", "403"):
            raise StorageError(f"Cloud storage refused the request (code {code}). Check the storage access key settings "
                               "and that the key has read and write permission.") from exc
        # The provider's error code is safe to show and makes problems diagnosable from a screenshot.
        raise StorageError(f"Cloud storage {action} failed (code {code or exc.__class__.__name__}).") from exc

    # ------------------------------------------------------------- interface
    MULTIPART_THRESHOLD = 8 * 1024 * 1024

    def put(self, key, data, content_type="application/octet-stream"):
        validate_key(key)
        try:
            if len(data) > self.MULTIPART_THRESHOLD and hasattr(self.client, "upload_fileobj"):
                # large files go up in 8 MB parts (multipart upload), which is more reliable
                import io

                from boto3.s3.transfer import TransferConfig

                cfg = TransferConfig(multipart_threshold=self.MULTIPART_THRESHOLD, multipart_chunksize=self.MULTIPART_THRESHOLD,
                                     max_concurrency=2, use_threads=True)
                self.client.upload_fileobj(io.BytesIO(data), self.bucket, key, ExtraArgs={"ContentType": content_type}, Config=cfg)
            else:
                self.client.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=content_type)
        except Exception as exc:
            self._fail("upload", key, exc)

    def get(self, key):
        validate_key(key)
        try:
            return self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read()
        except Exception as exc:
            self._fail("download", key, exc)

    def delete(self, key):
        validate_key(key)
        try:
            self.client.delete_object(Bucket=self.bucket, Key=key)
        except Exception as exc:
            if self._code(exc) in ("NoSuchKey", "404", "NotFound"):
                return
            self._fail("delete", key, exc)

    def exists(self, key):
        validate_key(key)
        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
            return True
        except Exception as exc:
            if self._code(exc) in ("NoSuchKey", "404", "NotFound"):
                return False
            self._fail("check", key, exc)

    def _list(self, prefix: str):
        token = None
        while True:
            kw = {"Bucket": self.bucket, "Prefix": prefix, "MaxKeys": 1000}
            if token:
                kw["ContinuationToken"] = token
            page = self.client.list_objects_v2(**kw)
            for o in page.get("Contents", []) or []:
                yield o
            if not page.get("IsTruncated"):
                break
            token = page.get("NextContinuationToken")

    def delete_prefix(self, prefix):
        validate_key(prefix.rstrip("/"))
        if not prefix.endswith("/"):
            prefix += "/"
        count = 0
        try:
            keys = [o["Key"] for o in self._list(prefix)]
            # one-by-one deletes work on every S3-compatible service (batch delete is not universal)
            for k in keys:
                self.client.delete_object(Bucket=self.bucket, Key=k)
                count += 1
        except Exception as exc:
            self._fail("delete", prefix, exc)
        return count

    def usage_bytes(self, prefix: str = "projects/") -> int:
        try:
            return sum(int(o.get("Size", 0)) for o in self._list(prefix))
        except Exception as exc:
            self._fail("list", prefix, exc)
            return 0
