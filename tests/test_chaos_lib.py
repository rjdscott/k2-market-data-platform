"""scripts/chaos/lib.sh: pause_lake_ingest and resume_lake_ingest against a fake
`docker` on PATH that answers the Airflow CLI the way Airflow 3.3.2 does
(`dags pause -o json` prints the DAGs it changed, with their old state, or
"No unpaused DAGs were found")."""

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# The driver shows in pgrep once, then the Airflow task is queued once (its
# driver not started yet), then the lake is idle.
FAKE_DOCKER = """#!/usr/bin/env bash
echo "$*" >> "$CALLS"
once() { if [ -e "$STATE.$1" ]; then return 1; fi; touch "$STATE.$1"; }
case "$*" in
  *"dags pause lake_ingest"*)
    if [ -n "${BY_HAND:-}" ]; then echo "No unpaused DAGs were found"
    else echo '[{"dag_id": "lake_ingest", "is_paused": "False"}]'; fi ;;
  *"dags unpause lake_ingest"*) echo '[{"dag_id": "lake_ingest", "is_paused": "True"}]' ;;
  *"dags list-runs lake_ingest --state running"*)
    echo '[{"dag_id": "lake_ingest", "run_id": "scheduled__2026-11-25T10:01:00+00:00", "state": "running"}]' ;;
  *"tasks states-for-dag-run lake_ingest scheduled__2026-11-25T10:01:00+00:00"*)
    if once ti; then s=queued; else s=success; fi
    echo "[{\\"dag_id\\": \\"lake_ingest\\", \\"task_id\\": \\"ingest\\", \\"state\\": \\"$s\\"}]" ;;
  *pgrep*) once pgrep || exit 1 ;;
esac
exit 0
"""


def run(tmp_path, script, **env):
    fake = tmp_path / "docker"
    fake.write_text(FAKE_DOCKER)
    fake.chmod(0o755)
    out = subprocess.run(
        ["bash", "-c", f"set -euo pipefail; . scripts/chaos/lib.sh; {script}"],
        cwd=ROOT, capture_output=True, text=True, timeout=30,
        env={**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}",
             "STATE": str(tmp_path / "seen"), "CALLS": str(tmp_path / "calls"),
             "K2_LAKE_WAIT_STEP": "0", **env},
    )
    calls = (tmp_path / "calls").read_text().splitlines()
    return out, calls


def test_pause_waits_out_the_driver_and_a_queued_task_then_returns_zero(tmp_path):
    out, calls = run(tmp_path, "pause_lake_ingest; echo reached; resume_lake_ingest")
    assert out.returncode == 0, out.stderr
    assert "waiting for the in-flight lake job" in out.stderr
    assert "reached" in out.stdout
    # The Airflow DAG, in the scheduler, and nothing of Prefect's.
    assert any(c.startswith("exec -e PYTHONWARNINGS=ignore k2-airflow-scheduler airflow dags pause lake_ingest")
               for c in calls)
    assert not [c for c in calls if "prefect" in c]
    # The wait read the task twice: queued (wait), then success (go). A wait on
    # the driver alone would have started while the executor held the task.
    assert sum("states-for-dag-run" in c for c in calls) == 2
    # Paused before the wait, unpaused once, after it.
    pause = next(i for i, c in enumerate(calls) if "dags pause" in c)
    first_pgrep = next(i for i, c in enumerate(calls) if "pgrep" in c)
    assert pause < first_pgrep
    assert [c for c in calls if "dags unpause" in c] == [calls[-1]]


def test_resume_twice_unpauses_once(tmp_path):
    out, calls = run(tmp_path, "pause_lake_ingest; resume_lake_ingest; resume_lake_ingest")
    assert out.returncode == 0, out.stderr
    assert sum("dags unpause lake_ingest" in c for c in calls) == 1


def test_a_dag_paused_by_hand_is_left_paused(tmp_path):
    out, calls = run(tmp_path, "pause_lake_ingest; resume_lake_ingest", BY_HAND="1")
    assert out.returncode == 0, out.stderr
    assert "nothing to pause" in out.stderr
    assert not [c for c in calls if "dags unpause" in c]
    # Not ours to unpause, but a run in flight is still waited out.
    assert any("pgrep" in c for c in calls)
