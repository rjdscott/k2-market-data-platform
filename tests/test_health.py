"""scripts/health.sh step (3), the last lake_ingest run, read through the Airflow
CLI, step (1)'s check that every long-running service is running, step (2)'s
per-venue liveness (the capture's healthcheck and the records Redpanda
acknowledged, ClickHouse stood down under ADR-057), and step (4)'s order-book
switch, the capture's gauge against both copies of the registry. The whole
script runs against fakes on PATH: `git` points it at a scratch checkout,
`docker` answers the CLI the way Airflow 3.3.2 does (a run whose only task
skipped is `success`; the task says `skipped`) and compose the way Compose v2
does, and logs every call; `curl` gives Prometheus a fresh commit and each
venue's delivered records. Step (5), the backup's age, reads a STATUS.json each
test writes."""

import json
import os
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

FAKE_DOCKER = """#!/usr/bin/env bash
echo "$*" >> "$DOCKER_LOG"
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
  "compose ps -q capture-"*) case " ${CAPTURES:-} " in *" ${4#capture-} "*) echo "cid-${4#capture-}" ;; esac ;;
  "inspect -f {{.State.Running}}"*) echo true ;;
  "inspect -f {{range"*) echo k2net ;;
  "run --rm --network k2net"*)
    url=${@: -1}; venue=${url#http://capture-}; venue=${venue%%:*}; var="METRICS_$venue"
    printf '%s\n' "${!var:-}" ;;
  "exec k2-spark-iceberg cat /home/iceberg/config/instruments.yaml") printf '%s' "${SPARK_REGISTRY:-}" ;;
  # The capture's own healthcheck: "ok: ..." exits 0, anything else 1, as the binary does.
  "exec cid-"*" /k2-capture healthcheck")
    var="HC_${2#cid-}"; out=${!var:-ok: trade is the stalest, 1.0s behind}; echo "$out"
    [[ $out == ok:* ]]; exit $? ;;
esac
exit 0
"""
FAKE_GIT = '#!/usr/bin/env bash\necho "$TOP"\n'
FAKE_CURL = """#!/usr/bin/env bash
case "$*" in
  *k2_capture_records_delivered_total*) printf '%s\\n' "$DELIVERED" ;;
  *) echo '{"data": {"result": [{"value": [0, "60"]}]}}' ;;
esac
"""
VENUES = ("binance", "kraken", "coinbase")


def delivered(**records):
    """Prometheus's answer to step (2)'s query: records per venue, 120 for any not named."""
    return json.dumps({"status": "success", "data": {"resultType": "vector", "result": [
        {"metric": {"exchange": v}, "value": [0, str(records.get(v, 120))]}
        for v in VENUES if records.get(v, 120) is not None]}})


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


# Step (5): SKIP where `make backup-install` never ran; once the timer is there,
# the age of the last success in STATUS.json (scripts/backup-lake.sh) decides.
@pytest.mark.parametrize("timer, status, verdict", [
    (False, None, "SKIP (not set up"),
    (True, None, "FAIL (the timer is installed and no backup"),
    (True, {"status": "ok", "last_success_utc": 3}, "PASS"),
    (True, {"status": "ok", "last_success_utc": 27}, "FAIL (last successful backup 27h ago"),
    (True, {"status": "failed", "error": "disk full", "last_success_utc": 24}, "WARN (the last run failed: disk full;"),
    (True, {"status": "failed", "error": "disk full", "last_success_utc": None}, "FAIL (the timer is installed"),
])
def test_backup(tmp_path, timer, status, verdict):
    if timer:
        (tmp_path / ".config/systemd/user").mkdir(parents=True)
        (tmp_path / ".config/systemd/user/k2-backup.timer").write_text("")
    if status is not None:
        hours = status["last_success_utc"]
        if hours is not None:
            status["last_success_utc"] = (datetime.now(timezone.utc) - timedelta(hours=hours, minutes=5)).strftime(
                "%Y-%m-%dT%H:%M:%SZ")
        (tmp_path / "k2-backup").mkdir()
        (tmp_path / "k2-backup/STATUS.json").write_text(json.dumps(status))
    line, rc = run_health(tmp_path, prefix="(5) backup: ", RUNS=runs("success"), TASK="success", MAINT="[]")
    assert line.split(": ", 1)[1].startswith(verdict), line
    assert rc == (1 if verdict.startswith("FAIL") else 0), line


def run_health(tmp_path, prefix="(3) last lake_ingest run: ", host_registry=None, **env):
    top = tmp_path / "repo"
    (top / "scripts/chaos").mkdir(parents=True)
    if host_registry is not None:
        (top / "config").mkdir()
        (top / "config/instruments.yaml").write_text(host_registry)
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
        # HOME too: the backup check reads ~/.config/systemd/user, and this host's own must not leak in.
        env={**os.environ, "PATH": f"{bin_}:{os.environ['PATH']}", "TOP": str(top), "HOME": str(tmp_path),
             "K2_BACKUP_DIR": str(tmp_path / "k2-backup"), "DOCKER_LOG": str(tmp_path / "docker.log"),
             "K2_HEALTH_WAIT": "0", "RUNNING": "redpanda prometheus", "CONFIG": CONFIG,
             # Every venue live unless a test says otherwise: step (2) fails a stopped one.
             "CAPTURES": " ".join(VENUES), "DELIVERED": delivered(), **env},
    )
    (line,) = [ln for ln in out.stdout.splitlines() if ln.startswith(prefix)]
    return line, out.returncode


def registry(binance):
    return f"""version: 2

# Order-book (L2) capture per venue, required. false: trades only (ADR-050).
book_capture:
  binance: {binance}   # a comment the parse must skip
  kraken: false
  coinbase: false

instruments:
  binance: []
"""


def capture(book):
    gauge = "" if book is None else f'k2_capture_book_capture{{exchange="binance"}} {book}\n'
    return gauge + 'k2_capture_last_message_ts_seconds{exchange="binance",stream="trade"} 1\n'


# Binance's capture against the host file and Spark's copy, the other two serving
# no metrics (WARN). A capture not recreated, the reverse, a stale Spark mount, an
# image older than the gauge and a value PyYAML would read as a boolean each FAIL.
@pytest.mark.parametrize("name, book, host, spark, verdict", [
    ("all three agree, books off", 0, "false", "false", "PASS (books off"),
    ("all three agree, books on", 1, "true", "true", "PASS (books on"),
    ("the registry says off, the capture was never recreated", 1, "false", "false", "FAIL (process on"),
    ("the registry says on, the capture still runs off", 0, "true", "true", "FAIL (process off"),
    ("spark-iceberg reads a stale copy", 0, "false", "true", "FAIL (process off, host file off, spark-iceberg's copy on"),
    ("no gauge: the image predates ADR-050", None, "true", "true", "FAIL (capture-binance exports no"),
    ("a YAML 1.1 spelling", 1, "on", "on", "FAIL (config/instruments.yaml book_capture.binance is ?on"),
])
def test_order_book_switch(tmp_path, name, book, host, spark, verdict):
    line, rc = run_health(tmp_path, prefix="(4) binance book_capture: ", host_registry=registry(host),
                          METRICS_binance=capture(book), SPARK_REGISTRY=registry(spark),
                          RUNS=runs("success"), TASK="success", MAINT="[]")
    assert line.split(": ", 1)[1].startswith(verdict), (name, line)
    assert rc == (1 if verdict.startswith("FAIL") else 0), (name, line)
    if "stale copy" in name:
        assert "--no-deps spark-iceberg" in line and "--no-deps capture-binance" not in line, line
    if "never recreated" in name:
        assert "--no-deps capture-binance" in line, line


def test_a_capture_that_is_not_running_is_not_compared(tmp_path):
    line, rc = run_health(tmp_path, prefix="(4) kraken book_capture: ", host_registry=registry("false"),
                          CAPTURES="binance", METRICS_binance=capture(0), SPARK_REGISTRY=registry("false"),
                          RUNS=runs("success"), TASK="success", MAINT="[]")
    assert line.split(": ", 1)[1].startswith("WARN (no metrics from capture-kraken"), line
    # Not compared here, but the run fails: step (2) fails a venue whose capture is stopped.
    assert rc == 1, line


# Step (2), each venue: its capture's own healthcheck and the records Redpanda
# acknowledged from it in the last 2 minutes, through Prometheus. A venue that
# stops talking, a broker that stops acknowledging, a capture not running and a
# Prometheus that cannot answer each FAIL; nothing in it waits or warns.
@pytest.mark.parametrize("name, env, verdict", [
    ("talking and delivered", {}, "PASS (120 records delivered to Redpanda in 2 min; healthcheck ok: trade"),
    ("the venue went silent", {"HC_binance": "stale: trade 412.3s (limit 300s)"},
     "FAIL (capture-binance healthcheck: stale: trade 412.3s (limit 300s);"),
    ("the broker stopped acknowledging", {"DELIVERED": delivered(binance=0)},
     "FAIL (0 records delivered to Redpanda in 2 min"),
    ("Prometheus has no series for the venue", {"DELIVERED": delivered(binance=None)},
     "FAIL (Prometheus has no k2_capture_records_delivered_total for binance"),
    ("Prometheus did not answer", {"DELIVERED": ""}, "FAIL (Prometheus did not answer"),
    ("the capture is not running", {"CAPTURES": "kraken coinbase"}, "FAIL (capture-binance is not running"),
])
def test_venue_liveness(tmp_path, name, env, verdict):
    line, rc = run_health(tmp_path, prefix="(2) binance: ", RUNS=runs("success"), TASK="success", MAINT="[]", **env)
    assert line.split(": ", 1)[1].startswith(verdict), (name, line)
    assert rc == (1 if verdict.startswith("FAIL") else 0), (name, line)


# ADR-057: the ClickHouse half is stood down visibly, one SKIP line naming the
# ADR, and nothing the script runs reaches ClickHouse.
def test_clickhouse_is_skipped_not_queried(tmp_path):
    line, rc = run_health(tmp_path, prefix="(2) clickhouse gold.trades: ", RUNS=runs("success"), TASK="success",
                          MAINT="[]")
    assert line.split(": ", 1)[1].startswith("SKIP (the ClickHouse hot tier is paused, ADR-057"), line
    assert rc == 0, line
    calls = (tmp_path / "docker.log").read_text().splitlines()
    assert any("/k2-capture healthcheck" in c for c in calls), calls
    assert not [c for c in calls if "clickhouse" in c.lower()], calls
