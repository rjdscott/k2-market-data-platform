"""scripts/chaos/lib.sh: pause_lake_ingest and resume_lake_ingest against a fake
`docker` on PATH that answers the Airflow CLI the way Airflow 3.3.2 does
(`dags pause -o json` prints the DAGs it changed, with their old state, or
"No unpaused DAGs were found")."""

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# BY_HAND lists DAGs already paused. The driver shows in pgrep once; a
# lake_ingest task is queued once (its driver not started yet); with REBUILD
# set, a lake_rebuild task is running once. Then the lake is idle.
FAKE_DOCKER = """#!/usr/bin/env bash
echo "$*" >> "$CALLS"
once() { if [ -e "$STATE.$1" ]; then return 1; fi; touch "$STATE.$1"; }
case "$*" in
  *"dags pause "*|*"dags unpause "*)
    all="$*"; dag=${all##* dags *pause }; dag=${dag%% *}
    case " ${BY_HAND:-} " in
      *" $dag "*) [ "${all#*dags unpause}" = "$all" ] && echo "No unpaused DAGs were found" ;;
      *) echo "[{\\"dag_id\\": \\"$dag\\", \\"is_paused\\": \\"False\\"}]" ;;
    esac ;;
  *"dags list-runs lake_ingest --state running"*)
    echo '[{"dag_id": "lake_ingest", "run_id": "scheduled__2026-11-25T10:01:00+00:00", "state": "running"}]' ;;
  *"dags list-runs lake_rebuild --state running"*)
    if [ -n "${REBUILD:-}" ]; then echo '[{"dag_id": "lake_rebuild", "run_id": "manual__x", "state": "running"}]'
    else echo '[]'; fi ;;
  *"dags list-runs"*"--state running"*) echo '[]' ;;
  *"tasks states-for-dag-run lake_ingest scheduled__2026-11-25T10:01:00+00:00"*)
    if once ti; then s=queued; else s=success; fi
    echo "[{\\"dag_id\\": \\"lake_ingest\\", \\"task_id\\": \\"ingest\\", \\"state\\": \\"$s\\"}]" ;;
  *"tasks states-for-dag-run lake_rebuild manual__x"*)
    if once rebuild; then s=running; else s=success; fi
    echo "[{\\"dag_id\\": \\"lake_rebuild\\", \\"task_id\\": \\"rebuild\\", \\"state\\": \\"$s\\"}]" ;;
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


def did(calls, verb, dag):
    return sum(c.endswith(f"airflow dags {verb} {dag} -o json") for c in calls)


def test_pause_waits_out_the_driver_and_a_queued_task_then_returns_zero(tmp_path):
    out, calls = run(tmp_path, "pause_lake_ingest; echo reached; resume_lake_ingest")
    assert out.returncode == 0, out.stderr
    assert "waiting for the in-flight lake job" in out.stderr
    assert "reached" in out.stdout
    # The Airflow DAGs, in the scheduler, and nothing of Prefect's.
    assert any(c.startswith("exec -e PYTHONWARNINGS=ignore k2-airflow-scheduler airflow dags pause lake_ingest")
               for c in calls)
    assert not [c for c in calls if "prefect" in c]
    # The wait read the ingest task twice: queued (wait), then success (go). A
    # wait on the driver alone would have started while the executor held it.
    assert sum("states-for-dag-run lake_ingest" in c for c in calls) == 2
    # Paused before the wait, unpaused once each, after it.
    pause = next(i for i, c in enumerate(calls) if "dags pause" in c)
    first_pgrep = next(i for i, c in enumerate(calls) if "pgrep" in c)
    assert pause < first_pgrep
    assert all(i > first_pgrep for i, c in enumerate(calls) if "dags unpause" in c)


def test_maintenance_is_paused_and_unpaused_with_the_ingest(tmp_path):
    out, calls = run(tmp_path, "pause_lake_ingest; resume_lake_ingest")
    assert out.returncode == 0, out.stderr
    for dag in ("lake_ingest", "lake_maintenance"):
        assert (did(calls, "pause", dag), did(calls, "unpause", dag)) == (1, 1), dag


def test_the_wait_covers_rebuild_and_every_lake_dag(tmp_path):
    out, calls = run(tmp_path, "pause_lake_ingest; resume_lake_ingest", REBUILD="1")
    assert out.returncode == 0, out.stderr
    assert any("pgrep -f lake/(ingest|maintenance|rebuild)\\.py" in c for c in calls)
    for dag in ("lake_ingest", "lake_maintenance", "lake_rebuild"):
        assert any(f"dags list-runs {dag} --state running" in c for c in calls), dag
    # A running rebuild task alone kept the wait going for one more look.
    assert sum("states-for-dag-run lake_rebuild" in c for c in calls) == 2


def test_resume_twice_unpauses_once(tmp_path):
    out, calls = run(tmp_path, "pause_lake_ingest; resume_lake_ingest; resume_lake_ingest")
    assert out.returncode == 0, out.stderr
    assert did(calls, "unpause", "lake_ingest") == 1
    assert did(calls, "unpause", "lake_maintenance") == 1


def test_only_the_dags_this_run_paused_are_unpaused(tmp_path):
    # The ingest was paused by hand; maintenance was not.
    out, calls = run(tmp_path, "pause_lake_ingest; resume_lake_ingest", BY_HAND="lake_ingest")
    assert out.returncode == 0, out.stderr
    assert "lake_ingest is not an unpaused DAG" in out.stderr
    assert did(calls, "unpause", "lake_ingest") == 0
    assert did(calls, "unpause", "lake_maintenance") == 1
    # Not ours to unpause, but a run in flight is still waited out.
    assert any("pgrep" in c for c in calls)


def test_nothing_unpaused_when_both_were_paused_by_hand(tmp_path):
    out, calls = run(tmp_path, "pause_lake_ingest; resume_lake_ingest",
                     BY_HAND="lake_ingest lake_maintenance")
    assert out.returncode == 0, out.stderr
    assert "this test may race it" in out.stderr
    assert not [c for c in calls if "dags unpause" in c]
