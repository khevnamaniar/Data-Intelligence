"""Database migration runner.

Usage (from the backend folder):  python -m app.migrate

Applies every ``migrations/NNNN_*.sql`` file that has not been applied yet, in order,
and records it in the ``schema_migrations`` table. Safe to run repeatedly.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

from .db import Database, get_db, now

MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "migrations"


def to_sqlite(sql: str) -> str:
    """Translate the PostgreSQL DDL used in migrations into SQLite DDL (tests only)."""
    replacements = [
        (r"\bUUID\b", "TEXT"),
        (r"\bJSONB\b", "TEXT"),
        (r"\bTIMESTAMPTZ\b", "TEXT"),
        (r"\bDOUBLE PRECISION\b", "REAL"),
        (r"\bBIGINT\b", "INTEGER"),
        (r"VARCHAR\(\d+\)", "TEXT"),
        (r"DEFAULT now\(\)", "DEFAULT CURRENT_TIMESTAMP"),
        (r"DEFAULT TRUE", "DEFAULT 1"),
        (r"DEFAULT FALSE", "DEFAULT 0"),
    ]
    for pattern, repl in replacements:
        sql = re.sub(pattern, repl, sql)
    return sql


def split_statements(sql: str) -> list[str]:
    lines = [ln for ln in sql.splitlines() if not ln.strip().startswith("--")]
    return [s.strip() for s in "\n".join(lines).split(";") if s.strip()]


MIGRATION_LOCK_ID = 7_310_442_118  # arbitrary constant for pg_advisory_lock


def migrate(db: Database | None = None, verbose: bool = True) -> list[str]:
    """Apply new migrations only. Never drops or recreates existing tables."""
    db = db or get_db()
    if db.dialect == "postgres":  # pragma: no cover - needs PostgreSQL
        # Hold a lock so two starting instances can never run the same migration twice.
        with db.tx() as c:
            c.execute("SELECT pg_advisory_xact_lock(?)", [MIGRATION_LOCK_ID])
            return _migrate(db, c, verbose)
    return _migrate(db, None, verbose)


def status(db: Database | None = None) -> dict:
    db = db or get_db()
    files = sorted(p.stem for p in MIGRATIONS_DIR.glob("*.sql"))
    with db.tx() as c:
        c.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version VARCHAR(100) PRIMARY KEY, applied_at VARCHAR(50) NOT NULL)")
        done = [r["version"] for r in c.fetchall("SELECT version FROM schema_migrations ORDER BY version")]
    return {"applied": done, "pending": [f for f in files if f not in done]}


def _migrate(db: Database, locked_conn, verbose: bool) -> list[str]:
    applied: list[str] = []
    from contextlib import nullcontext

    def tx():
        return nullcontext(locked_conn) if locked_conn is not None else db.tx()

    with tx() as c:
        c.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations (version VARCHAR(100) PRIMARY KEY, applied_at VARCHAR(50) NOT NULL)"
        )
        done = {r["version"] for r in c.fetchall("SELECT version FROM schema_migrations")}
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        version = path.stem
        if version in done:
            continue
        sql = path.read_text(encoding="utf-8")
        if db.dialect == "sqlite":
            sql = to_sqlite(sql)
        if re.search(r"\b(DROP\s+(TABLE|SCHEMA|DATABASE)|TRUNCATE)\b", sql, re.I):
            raise RuntimeError(f"Migration {version} contains a destructive statement and was refused.")
        with tx() as c:
            for stmt in split_statements(sql):
                c.execute(stmt)
            c.execute("INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)", [version, now()])
        applied.append(version)
        if verbose:
            print(f"Applied migration {version}")
    if verbose and not applied:
        print("Database is already up to date.")
    return applied


if __name__ == "__main__":
    if "--status" in sys.argv:
        st = status()
        print(f"Applied: {', '.join(st['applied']) or 'none'}")
        print(f"Pending: {', '.join(st['pending']) or 'none'}")
        sys.exit(0)
    try:
        migrate()
    except Exception as exc:  # pragma: no cover
        print(f"Migration failed: {exc}", file=sys.stderr)
        sys.exit(1)
