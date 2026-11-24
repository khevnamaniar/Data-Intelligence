"""Small database layer.

Production/local development uses PostgreSQL (Neon) through psycopg 3.
The automated test-suite can run against SQLite (``sqlite:///...``) so it works
without any network database; the schema and SQL used here are portable.

All SQL in the application is written with ``?`` placeholders and parameters are
always passed separately (never string-formatted), which prevents SQL injection.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from typing import Any, Iterator

from .config import settings


def new_id() -> str:
    return str(uuid.uuid4())


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def clean_json(o: Any) -> Any:
    """Recursively make a value JSON-safe: NaN/inf -> None, numpy/datetime/UUID -> plain types."""
    import math

    if isinstance(o, dict):
        return {str(k): clean_json(v) for k, v in o.items()}
    if isinstance(o, (list, tuple, set)):
        return [clean_json(v) for v in o]
    if isinstance(o, float):
        return None if math.isnan(o) or math.isinf(o) else o
    if isinstance(o, (str, int, bool)) or o is None:
        return o
    v = _json_default(o)
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
    return v


def to_json(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(clean_json(value), allow_nan=False)


def _json_default(o: Any):
    if isinstance(o, (dt.datetime, dt.date)):
        return o.isoformat()
    if isinstance(o, uuid.UUID):
        return str(o)
    try:  # numpy scalars
        import numpy as np

        if isinstance(o, np.integer):
            return int(o)
        if isinstance(o, np.floating):
            return None if np.isnan(o) else float(o)
        if isinstance(o, np.bool_):
            return bool(o)
    except Exception:  # pragma: no cover
        pass
    return str(o)


def _normalize_row(row: dict) -> dict:
    from decimal import Decimal

    out = {}
    for k, v in row.items():
        if isinstance(v, uuid.UUID):
            v = str(v)
        elif isinstance(v, Decimal):
            v = int(v) if v == v.to_integral_value() else float(v)
        elif isinstance(v, (dt.datetime, dt.date)):
            v = v.isoformat()
        elif k.endswith("_json") and isinstance(v, (str, bytes)):
            try:
                v = json.loads(v)
            except ValueError:
                pass
        out[k] = v
    return out


class Connection:
    """Thin wrapper giving both drivers the same API."""

    def __init__(self, raw, dialect: str):
        self.raw = raw
        self.dialect = dialect

    def _sql(self, sql: str) -> str:
        if self.dialect == "postgres":
            return sql.replace("?", "%s")
        return sql

    def _params(self, params):
        if params is None:
            return ()
        fixed = []
        for p in params:
            if isinstance(p, bool) and self.dialect == "sqlite":
                p = int(p)
            elif p is not None and type(p).__module__ == "numpy":  # numpy scalars -> plain Python
                p = p.item() if hasattr(p, "item") else p
            fixed.append(p)
        return tuple(fixed)

    def execute(self, sql: str, params=None) -> int:
        cur = self.raw.cursor()
        cur.execute(self._sql(sql), self._params(params))
        count = cur.rowcount
        cur.close()
        return count

    def fetchall(self, sql: str, params=None) -> list[dict]:
        cur = self.raw.cursor()
        cur.execute(self._sql(sql), self._params(params))
        cols = [d[0] for d in cur.description] if cur.description else []
        rows = [_normalize_row(dict(zip(cols, r))) for r in cur.fetchall()]
        cur.close()
        return rows

    def fetchone(self, sql: str, params=None) -> dict | None:
        rows = self.fetchall(sql, params)
        return rows[0] if rows else None

    def insert(self, table: str, values: dict) -> dict:
        values = dict(values)
        values.setdefault("id", new_id())
        for k, v in list(values.items()):
            if k.endswith("_json") and v is not None and not isinstance(v, str):
                values[k] = to_json(v)
        cols = ", ".join(values.keys())
        marks = ", ".join("?" for _ in values)
        self.execute(f"INSERT INTO {table} ({cols}) VALUES ({marks})", list(values.values()))
        return values

    def update(self, table: str, row_id: str, values: dict) -> int:
        values = dict(values)
        for k, v in list(values.items()):
            if k.endswith("_json") and v is not None and not isinstance(v, str):
                values[k] = to_json(v)
        sets = ", ".join(f"{k} = ?" for k in values)
        return self.execute(f"UPDATE {table} SET {sets} WHERE id = ?", [*values.values(), row_id])


class DatabaseUnavailable(Exception):
    """Raised when the database cannot be reached; shown to users as a plain-language message."""


def postgres_conninfo(url: str) -> str:
    """Normalise a Neon/PostgreSQL URL: accept postgres://, and always require TLS (SSL)."""
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if "sslmode=" not in url:
        url += ("&" if "?" in url else "?") + "sslmode=require"
    return url


def describe_db_target(url: str) -> str:
    """A log-safe description of the database (host only — never the password)."""
    import urllib.parse

    try:
        p = urllib.parse.urlsplit(url)
        return f"PostgreSQL at {p.hostname} / database '{(p.path or '/').lstrip('/') or '?'}'" if p.hostname else "PostgreSQL"
    except ValueError:
        return "PostgreSQL"


class Database:
    def __init__(self, url: str):
        if not url:
            raise RuntimeError(
                "DATABASE_URL is not set. See README.md → 'Configure your local environment'."
            )
        self.url = url
        self._pool = None
        if url.startswith("sqlite"):
            self.dialect = "sqlite"
            path = url.split("sqlite:///", 1)[-1] or ":memory:"
            self._sqlite_path = path
            self._lock = threading.RLock()
            # A single shared connection keeps in-memory databases alive for tests.
            self._sqlite_conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
            self._sqlite_conn.execute("PRAGMA foreign_keys = ON")
        elif url.startswith(("postgres://", "postgresql://")):
            self.dialect = "postgres"
            try:
                import psycopg  # noqa: F401
            except ImportError as exc:  # pragma: no cover
                raise RuntimeError("psycopg is required for PostgreSQL. Run: pip install -r requirements.txt") from exc
            self.conninfo = postgres_conninfo(url)
        else:
            raise RuntimeError("DATABASE_URL must start with postgresql:// (Neon) or sqlite:/// (tests only).")

    # ------------------------------------------------------------- postgres helpers
    @staticmethod
    def _configure(conn) -> None:  # pragma: no cover - needs PostgreSQL
        """Send Python strings as 'unknown' type so PostgreSQL casts them to uuid/jsonb/timestamptz as needed."""
        try:
            from psycopg.types.string import StrDumperUnknown

            conn.adapters.register_dumper(str, StrDumperUnknown)
        except Exception:
            pass

    def _connect_kwargs(self) -> dict:
        # prepare_threshold=None: no server-side prepared statements, which keeps Neon's
        # connection pooler (PgBouncer, transaction mode) happy.
        return {"connect_timeout": 15, "prepare_threshold": None, "application_name": "data-intelligence-platform"}

    def _get_pool(self):  # pragma: no cover - needs PostgreSQL
        if self._pool is None:
            try:
                from psycopg_pool import ConnectionPool
            except ImportError:
                return None
            self._pool = ConnectionPool(
                self.conninfo, min_size=0, max_size=int(os.getenv("DB_POOL_SIZE", "5")), kwargs=self._connect_kwargs(),
                configure=self._configure, check=ConnectionPool.check_connection, max_idle=120, timeout=20, open=True,
                name="dip-pool")
        return self._pool

    @contextmanager
    def tx(self) -> Iterator[Connection]:
        """Open a transaction; commits on success, rolls back on error."""
        if self.dialect == "sqlite":
            with self._lock:
                conn = self._sqlite_conn
                conn.execute("BEGIN")
                try:
                    yield Connection(conn, "sqlite")
                    conn.execute("COMMIT")
                except BaseException:
                    conn.execute("ROLLBACK")
                    raise
            return
        # PostgreSQL path (Neon)
        import psycopg

        pool = self._get_pool()
        try:
            if pool is not None:
                with pool.connection() as conn:
                    try:
                        yield Connection(conn, "postgres")
                        conn.commit()
                    except BaseException:
                        conn.rollback()
                        raise
            else:
                conn = psycopg.connect(self.conninfo, **self._connect_kwargs())
                self._configure(conn)
                try:
                    yield Connection(conn, "postgres")
                    conn.commit()
                except BaseException:
                    conn.rollback()
                    raise
                finally:
                    conn.close()
        except psycopg.OperationalError as exc:
            raise DatabaseUnavailable(str(exc)) from exc
        except Exception as exc:
            if exc.__class__.__name__ in ("PoolTimeout", "TooManyConnections"):
                raise DatabaseUnavailable(str(exc)) from exc
            raise

    def ping(self) -> bool:
        with self.tx() as c:
            c.fetchone("SELECT 1 AS ok")
        return True

    def close(self) -> None:
        if self._pool is not None:  # pragma: no cover
            self._pool.close()
            self._pool = None


_db: Database | None = None


def get_db() -> Database:
    global _db
    if _db is None:
        _db = Database(settings.database_url)
    return _db


def set_db(db: Database | None) -> None:
    """Used by tests to inject a database."""
    global _db
    _db = db
