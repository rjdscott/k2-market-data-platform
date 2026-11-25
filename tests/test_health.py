"""scripts/health.sh step (3), the last lake_ingest run, read through the Airflow
CLI. The whole script runs against fakes on PATH: `git` points it at a scratch
checkout, `docker` answers the CLI the way Airflow 3.3.2 does (a run whose only
task skipped is `success`; the task says `skipped`), and `curl` gives
Prometheus a fresh commit."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

FAKE_DOCKER = """#!/usr/bin/env bash
case "$*" in
  "compose ps --format"*) echo "k2-redpanda healthy" ;;
  *"airflow dags list-runs lake_ingest -o json --start-date"*) echo "$RUNS" ;;
  *"airflow tasks states-for-dag-run lake_ingest"*)
    echo "[{\\"dag_id\\": \\"lake_ingest\\", \\"task_id\\": \\"ingest\\", \\"state\\": \\"$TASK\\"}]" ;;
  *"airflow dags list-runs lake_maintenance --state running"*) echo "$MAINT" ;;
  *"airflow dags list-runs lake_rebuild --state running"*) echo "[]" ;;
  *"airflow tasks states-for-dag-run lake_maintenance"*)
    echo '[{"dag_id": "lake_maintenance", "task_id": "maintain", "state": "running"}]' ;;
esac
exit 0
"""
FAKE_GIT = '#!/usr/bin/env bash\necho "$TOP"\n'
FAKE_CURL = """#!/usr/bin/env bash
echo '{"data": {"result": [{"value": [0, "60"]}]}}'
"""


def runs(*states):
    return json.dumps([{"run_id": f"scheduled__2026-11-25T10:{56 - 5 * i:02d}:00+00:00", "state": s}
                       for i, s in enumerate(states)])


MAINT_RUNNING = json.dumps([{"run_id": "scheduled__2026-11-25T03:00:00+00:00", "state": "running"}])


@pytest.mark.parametrize("name, runs_json, task, maint, verdict", [
    ("a real commit", runs("success"), "success", "[]", "PASS"),
    ("the newest run failed", runs("failed", "success"), "success", "[]", "FAIL"),
    ("two skips, the lock doing its job", runs("success", "success"), "skipped", "[]", "WARN"),
    ("three skips, nothing of Airflow's holding the lock", runs("success", "success", "success"),
     "skipped", "[]", "FAIL"),
    ("three skips while lake_maintenance runs", runs("success", "success", "success"),
     "skipped", MAINT_RUNNING, "PASS"),
    ("no run finished in the window", "[]", "success", "[]", "FAIL"),
    ("the scheduler did not answer", "", "success", "[]", "FAIL"),
])
def test_last_lake_ingest_run(tmp_path, name, runs_json, task, maint, verdict):
    top = tmp_path / "repo"
    (top / "scripts/chaos").mkdir(parents=True)
    shutil.copy(ROOT / "scripts/chaos/lib.sh", top / "scripts/chaos/lib.sh")
    (top / "docker-compose.yml").write_text("")
    (top / ".env").write_text("")
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    for tool, body in (("docker", FAKE_DOCKER), ("git", FAKE_GIT), ("curl", FAKE_CURL)):
        (bin_ / tool).write_text(body)
        (bin_ / tool).chmod(0o755)
    out = subprocess.run(
        ["bash", str(ROOT / "scripts/health.sh")], capture_output=True, text=True, timeout=60,
        env={**os.environ, "PATH": f"{bin_}:{os.environ['PATH']}", "TOP": str(top),
             "RUNS": runs_json, "TASK": task, "MAINT": maint, "K2_HEALTH_WAIT": "0"},
    )
    (line,) = [ln for ln in out.stdout.splitlines() if ln.startswith("(3) last lake_ingest run: ")]
    assert line.split(": ", 1)[1].startswith(verdict), (name, line)
    # Exit status follows the FAIL lines; the rest of this fake stack passes.
    assert out.returncode == (1 if verdict == "FAIL" else 0), out.stdout
