from __future__ import annotations

import re
from abc import ABC, abstractmethod


class StorageError(Exception):
    pass


_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-/]{0,900}$")


def validate_key(key: str) -> str:
    if not _KEY_RE.match(key) or ".." in key or key.startswith("/") or "//" in key:
        raise StorageError(f"Invalid storage key: {key!r}")
    return key


class StorageProvider(ABC):
    """Interface every storage backend implements.

    Keys look like ``projects/<project_id>/files/<file_id>/original.pdf``.
    Implementations must never expose objects publicly.
    """

    name: str = "abstract"

    @abstractmethod
    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None: ...

    @abstractmethod
    def get(self, key: str) -> bytes: ...

    @abstractmethod
    def delete(self, key: str) -> None: ...

    @abstractmethod
    def exists(self, key: str) -> bool: ...

    def usage_bytes(self, prefix: str = "projects/") -> int | None:
        """Total bytes stored under a prefix (None if the provider cannot tell)."""
        return None

    @abstractmethod
    def delete_prefix(self, prefix: str) -> int:
        """Delete every object whose key starts with ``prefix``. Returns count deleted."""
