"""The one write lock for the lake, shared by ingest.py, maintenance.py and file_source.py.

ingest.py takes it non-blocking and exits 75 (EX_TEMPFAIL) if held: two appends must never
interleave (docker/lake/README.md, "Concurrency must be 1"). maintenance.py
takes it blocking, waits out a running ingest, and holds it for its whole run,
so the only ingest that could share the 8 GiB container with its 2g driver is
refused at the lock instead of OOM-killed halfway through a commit.
file_source.py waits a bounded time (`wait_s`), then exits 75 for its DAG to retry.

Pure stdlib, no pyspark, so tests import it directly.
"""

from __future__ import annotations

import fcntl
import os
import time

LOCK_PATH = os.environ.get("K2_LAKE_LOCK", "/tmp/k2-lake-ingest.lock")  # noqa: S108


def acquire_lock(path: str = LOCK_PATH, *, blocking: bool = False, wait_s: float | None = None):
    """Returns the held handle, or None if another writer holds it and
    ``blocking`` is False. The handle is returned so the caller keeps it open:
    the lock dies with the file descriptor, and with the process, so a SIGKILL
    releases it without leaving a stale lock behind.

    ``wait_s`` waits at most that many seconds, trying once a second, and
    returns None if the lock is still held then."""
    handle = open(path, "w")  # noqa: SIM115 - held for the life of the run
    deadline = None if wait_s is None else time.monotonic() + wait_s
    while True:
        try:
            nb = 0 if blocking and deadline is None else fcntl.LOCK_NB
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | nb)
            return handle
        except OSError:
            if deadline is None or time.monotonic() >= deadline:
                handle.close()
                return None
            time.sleep(1)
