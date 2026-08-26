"""The one write lock for the lake, shared by ingest.py and maintenance.py.

ingest.py takes it non-blocking and exits 2 if held: two appends must never
interleave (docker/lake/README.md, "Concurrency must be 1"). maintenance.py
takes it blocking, waits out a running ingest, and holds it for its whole run,
so the only ingest that could share the 4 GiB container with its 2g driver is
refused at the lock instead of OOM-killed halfway through a commit.

Pure stdlib, no pyspark, so tests import it directly.
"""

from __future__ import annotations

import fcntl
import os

LOCK_PATH = os.environ.get("K2_LAKE_LOCK", "/tmp/k2-lake-ingest.lock")  # noqa: S108


def acquire_lock(path: str = LOCK_PATH, *, blocking: bool = False):
    """Returns the held handle, or None if another writer holds it and
    ``blocking`` is False. The handle is returned so the caller keeps it open —
    the lock dies with the file descriptor, and with the process, so a SIGKILL
    releases it without leaving a stale lock behind."""
    handle = open(path, "w")  # noqa: SIM115 - held for the life of the run
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
    except OSError:
        handle.close()
        return None
    return handle
