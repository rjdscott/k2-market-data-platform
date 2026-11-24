"""scripts/chaos/lib.sh: pause_lake_ingest must return 0 after waiting out a job."""

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# A fake `docker`: one active schedule, and `pgrep` finds a lake job exactly once.
FAKE_DOCKER = """#!/usr/bin/env bash
echo "$*" >> "$CALLS"
case "$*" in
  *"work-queue inspect"*) echo '{"name": "default", "is_paused": false}' ;;
  *"schedule ls"*) echo '[{"id": "abc", "active": true}]' ;;
  *pgrep*) if [ -e "$FLAG" ]; then exit 1; else touch "$FLAG"; exit 0; fi ;;
esac
exit 0
"""


def test_pause_returns_zero_after_waiting_for_an_in_flight_job(tmp_path):
    fake = tmp_path / "docker"
    fake.write_text(FAKE_DOCKER)
    fake.chmod(0o755)
    run = subprocess.run(
        ["bash", "-c", "set -euo pipefail; . scripts/chaos/lib.sh; pause_lake_ingest; echo reached; resume_lake_ingest"],
        cwd=ROOT, capture_output=True, text=True, timeout=30,
        env={**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}",
             "FLAG": str(tmp_path / "seen"), "CALLS": str(tmp_path / "calls"),
             "K2_LAKE_WAIT_STEP": "0"},
    )
    assert run.returncode == 0, run.stderr
    assert "waiting for the in-flight lake job" in run.stderr  # the wait path ran
    assert "reached" in run.stdout
    # The queue is paused as well as the schedule (past-due runs ignore a
    # schedule pause), and the same run that paused it resumes it.
    calls = (tmp_path / "calls").read_text()
    assert "work-queue pause default --pool lake" in calls
    assert "work-queue resume default --pool lake" in calls
    assert "schedule resume" in calls
