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


_RUN_INGEST = """
import runpy, sys
from unittest import mock
# Spark and the stages are not needed: the lock check runs before any session.
for name in ("pyspark", "pyspark.sql", "pyspark.sql.functions", "books", "bronze",
             "gold", "silver", "catalog", "spark_conf"):
    sys.modules[name] = mock.MagicMock()
sys.argv = ["ingest.py"]
runpy.run_path("docker/lake/ingest.py", run_name="__main__")
"""


def test_ingest_exits_75_when_the_writer_lock_is_held(tmp_path):
    path = str(tmp_path / "lake.lock")
    holder = L.acquire_lock(path)
    try:
        run = subprocess.run(
            [sys.executable, "-c", _RUN_INGEST],
            env={**os.environ, "K2_LAKE_LOCK": path, "PYTHONPATH": "docker/lake"},
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            capture_output=True, text=True, timeout=30,
        )
    finally:
        holder.close()
    assert run.returncode == 75, run.stderr  # EX_TEMPFAIL, not 2 (argparse's usage-error code)
    assert "holds" in run.stderr
