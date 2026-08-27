"""docker/lake/lock.py — the flock both lake writers share."""

import os
import subprocess
import sys

import lock as L


def test_second_nonblocking_taker_is_refused_while_first_holds(tmp_path):
    path = str(tmp_path / "lake.lock")
    first = L.acquire_lock(path)
    assert first is not None
    assert L.acquire_lock(path) is None
    first.close()
    assert L.acquire_lock(path) is not None


def test_blocking_taker_waits_for_holder_to_exit(tmp_path):
    # A separate process holds the lock briefly; the blocking caller must get
    # it afterwards, not immediately (that would be the OOM path) and not never.
    path = str(tmp_path / "lake.lock")
    holder = subprocess.Popen(
        [sys.executable, "-c",
         f"import fcntl,time; h=open({path!r},'w'); fcntl.flock(h, fcntl.LOCK_EX); "
         "print('held', flush=True); time.sleep(1.5)"],
        stdout=subprocess.PIPE, text=True,
    )
    assert holder.stdout.readline().strip() == "held"
    assert L.acquire_lock(path) is None
    got = L.acquire_lock(path, blocking=True)
    assert got is not None
    assert holder.wait(timeout=5) == 0
    assert os.path.exists(path)
