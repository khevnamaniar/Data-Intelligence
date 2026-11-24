"""Local-disk storage for development and tests.

Files are kept in LOCAL_STORAGE_DIR (default ``.data/storage`` in the repository,
which is git-ignored). This is not intended for production on Render, whose disk is
wiped on each deploy — Phase 2 switches to a cloud provider (see storage/s3.py).
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

from .base import StorageError, StorageProvider, validate_key


class LocalStorageProvider(StorageProvider):
    name = "local"

    def __init__(self, root: str):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        validate_key(key)
        path = (self.root / key).resolve()
        if self.root not in path.parents and path != self.root:
            raise StorageError("Storage key escapes the storage root.")
        return path

    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, path)

    def get(self, key: str) -> bytes:
        path = self._path(key)
        if not path.is_file():
            raise StorageError(f"Stored object not found: {key}")
        return path.read_bytes()

    def delete(self, key: str) -> None:
        path = self._path(key)
        if path.is_file():
            path.unlink()

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def delete_prefix(self, prefix: str) -> int:
        validate_key(prefix.rstrip("/"))
        base = self._path(prefix.rstrip("/"))
        if base.is_dir():
            count = sum(1 for p in base.rglob("*") if p.is_file())
            shutil.rmtree(base)
            return count
        if base.is_file():
            base.unlink()
            return 1
        return 0

    def usage_bytes(self, prefix: str = "projects/") -> int:
        base = self.root / prefix.rstrip("/")
        return sum(f.stat().st_size for f in base.rglob("*") if f.is_file()) if base.exists() else 0
