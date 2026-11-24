"""File storage abstraction.

Uploaded files are never served directly from disk or a public URL; they are only
read back through authenticated API endpoints. ``get_storage()`` returns the
provider selected by the STORAGE_PROVIDER environment variable.
"""
from __future__ import annotations

from .base import StorageError, StorageProvider


class StorageQuotaError(StorageError):
    """Raised when the configured or provider storage limit is reached."""
from .local import LocalStorageProvider

_storage: StorageProvider | None = None


def get_storage() -> StorageProvider:
    global _storage
    if _storage is None:
        from ..config import settings

        name = settings.storage_provider.lower()
        if name == "local":
            _storage = LocalStorageProvider(settings.local_storage_dir)
        elif name in ("s3", "neon", "r2"):
            from .s3 import S3StorageProvider

            _storage = S3StorageProvider.from_env()
        else:
            raise StorageError(f"Unknown STORAGE_PROVIDER '{settings.storage_provider}'. Use 's3' (cloud) or 'local' (development only).")
    return _storage


def set_storage(provider: StorageProvider | None) -> None:
    global _storage
    _storage = provider


__all__ = ["StorageProvider", "StorageError", "StorageQuotaError", "LocalStorageProvider", "get_storage", "set_storage"]
