"""Background job runner (in-process thread pool).

Long work (file processing, analysis) runs in a background thread so the web request
returns immediately; the frontend polls for status. Set ANALYSIS_SYNC=true to run jobs
inline (used by tests). A durable queue can replace this later without changing callers.
"""
from __future__ import annotations

import logging
import os
from concurrent.futures import ThreadPoolExecutor

from ..config import settings

log = logging.getLogger("dip.jobs")
# One job at a time by default: keeps peak memory well inside a 512 MB free server
# (measured peak for the 9-file demo + deep analysis + exports: ~330 MB).
_pool = ThreadPoolExecutor(max_workers=max(1, int(os.getenv("JOB_WORKERS", "1"))), thread_name_prefix="dip-job")


def submit(fn, *args, **kwargs):
    if settings.analysis_sync:
        return fn(*args, **kwargs)

    def wrapped():
        try:
            fn(*args, **kwargs)
        except Exception:  # pragma: no cover - logged, status recorded by the job itself
            log.exception("Background job failed")

    _pool.submit(wrapped)
    return None
