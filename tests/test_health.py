"""scripts/health.sh step (3), the last lake_ingest run, read through the Airflow
CLI, and step (1)'s check that every long-running service is running. The whole
script runs against fakes on PATH: `git` points it at a scratch checkout,
`docker` answers the CLI the way Airflow 3.3.2 does (a run whose only task
skipped is `success`; the task says `skipped`) and compose the way Compose v2
does, and `curl` gives Prometheus a fresh commit."""

import json
import os
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

FAKE_DOCKER = """#!/usr/bin/env bash
case "$*" in
  "compose ps --format"*) echo "k2-redpanda healthy" ;;
  "compose ps --services --status running"*) echo "$RUNNING" ;;
  "compose config --format json"*) echo "$CONFIG" ;;
  *"airflow dags list-runs lake_ingest -o json --start-date"*) echo "$RUNS" ;;
  *"airflow tasks states-for-dag-run lake_ingest"*)
    echo "[{\\"dag_id\\": \\"lake_ingest\\", \\"task_id\\": \\"ingest\\", \\"state\\": \\"$TASK\\"}]" ;;
  *"airflow dags list-runs lake_maintenance --state running"*) echo "$MAINT" ;;
  *"airflow dags list-runs lake_maintenance -o json"*) echo "${MAINT_ALL:-[]}" ;;
  *"airflow dags list-runs lake_rebuild"*) echo "[]" ;;
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

# Two long-running services and a one-shot, as `docker compose config --format json` gives them.
CONFIG = json.dumps({"name": "repo", "services": {
    "redpanda": {"restart": "unless-stopped"}, "prometheus": {"restart": "unless-stopped"},
    "redpanda-init": {"restart": "no"}}})


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
    line, rc = run_health(tmp_path, RUNS=runs_json, TASK=task, MAINT=maint)
    assert line.split(": ", 1)[1].startswith(verdict), (name, line)
    # Exit status follows the FAIL lines; the rest of this fake stack passes.
    assert rc == (1 if verdict == "FAIL" else 0), line


# Three skips, and lake_maintenance no longer running: it ended 5 minutes ago,
# inside AirflowLakeIngestSkippedRepeatedly's 15-minute grace, so its skips are
# still the newest runs (observed 2026-11-25, 06:34 to 06:39 UTC), or 30
# minutes ago, when they are a stuck lock.
@pytest.mark.parametrize("ended_min_ago, verdict", [(5, "PASS"), (30, "FAIL")])
def test_skips_after_a_holder_ended(tmp_path, ended_min_ago, verdict):
    end = datetime.now(timezone.utc) - timedelta(minutes=ended_min_ago)
    maint_all = json.dumps([{"run_id": "scheduled__2026-11-25T03:00:00+00:00", "state": "success",
                             "end_date": end.isoformat()}])
    line, _ = run_health(tmp_path, RUNS=runs("success", "success", "success"), TASK="skipped",
                         MAINT="[]", MAINT_ALL=maint_all)
    assert line.split(": ", 1)[1].startswith(verdict), line


# Prometheus exited and so absent from `compose ps` (2026-11-25, after the VM
# restart): step (1)'s health loop passes, the long-running check must not. The
# exited one-shot is never a failure, and an unreadable compose file is.
@pytest.mark.parametrize("running, config, verdict", [
    ("redpanda prometheus", CONFIG, "PASS"),
    ("redpanda", CONFIG, "FAIL (not running: prometheus;"),
    ("redpanda prometheus", "not json", "FAIL (could not read"),
])
def test_long_running_service_not_running(tmp_path, running, config, verdict):
    line, rc = run_health(tmp_path, prefix="(1) long-running services: ", RUNNING=running, CONFIG=config,
                          RUNS=runs("success"), TASK="success", MAINT="[]")
    assert line.split(": ", 1)[1].startswith(verdict), line
    assert rc == (1 if verdict.startswith("FAIL") else 0), line


def run_health(tmp_path, prefix="(3) last lake_ingest run: ", **env):
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
             "K2_HEALTH_WAIT": "0", "RUNNING": "redpanda prometheus", "CONFIG": CONFIG, **env},
    )
    (line,) = [ln for ln in out.stdout.splitlines() if ln.startswith(prefix)]
    return line, out.returncode
