"""scripts/health.sh step (1)'s check that every long-running service is running,
step (2)'s per-venue liveness (the capture's healthcheck and the records Redpanda
acknowledged, ClickHouse stood down under ADR-057), step (3), the lake, stood
down until phase 004, and step (4)'s order-book switch, the capture's gauge
against the registry. The whole script runs against fakes on PATH: `git` points
it at a scratch checkout, `docker` answers compose the way Compose v2 does, and
`docker` and `curl` log every call; `curl` gives Prometheus each venue's
delivered records. Step (5), the backup's age, reads a STATUS.json each test
writes."""

import json
import os
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
  "compose ps -q capture-"*) case " ${CAPTURES:-} " in *" ${4#capture-} "*) echo "cid-${4#capture-}" ;; esac ;;
  "inspect -f {{.State.Running}}"*) echo true ;;
  "inspect -f {{range"*) echo k2net ;;
  "run --rm --network k2net"*)
    url=${@: -1}; venue=${url#http://capture-}; venue=${venue%%:*}; var="METRICS_$venue"
    printf '%s\n' "${!var:-}" ;;
  # The capture's own healthcheck: "ok: ..." exits 0, anything else 1, as the binary does.
  "exec cid-"*" /k2-capture healthcheck")
    var="HC_${2#cid-}"; out=${!var:-ok: trade is the stalest, 1.0s behind}; echo "$out"
    [[ $out == ok:* ]]; exit $? ;;
esac
exit 0
"""
FAKE_GIT = '#!/usr/bin/env bash\necho "$TOP"\n'
FAKE_CURL = """#!/usr/bin/env bash
echo "$*" >> "$CURL_LOG"
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


# Two long-running services and a one-shot, as `docker compose config --format json` gives them.
CONFIG = json.dumps({"name": "repo", "services": {
    "redpanda": {"restart": "unless-stopped"}, "prometheus": {"restart": "unless-stopped"},
    "redpanda-init": {"restart": "no"}}})


# The old lake ingest is retired (ADR-056): one SKIP line naming phase 004, and
# nothing the script runs asks for a lake commit, an Airflow run or Spark.
def test_lake_is_skipped_not_queried(tmp_path):
    line, rc = run_health(tmp_path)
    assert line.split(": ", 1)[1].startswith("SKIP (commit age and last ingest run"), line
    assert "phase 004" in line and rc == 0, line
    calls = (tmp_path / "docker.log").read_text() + (tmp_path / "curl.log").read_text()
    assert "k2_lake" not in calls and "airflow" not in calls and "spark-iceberg" not in calls, calls


# Prometheus exited and so absent from `compose ps` (2026-11-25, after the VM
# restart): step (1)'s health loop passes, the long-running check must not. The
# exited one-shot is never a failure, and an unreadable compose file is.
@pytest.mark.parametrize("running, config, verdict", [
    ("redpanda prometheus", CONFIG, "PASS"),
    ("redpanda", CONFIG, "FAIL (not running: prometheus;"),
    ("redpanda prometheus", "not json", "FAIL (could not read"),
])
def test_long_running_service_not_running(tmp_path, running, config, verdict):
    line, rc = run_health(tmp_path, prefix="(1) long-running services: ", RUNNING=running, CONFIG=config)
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
    line, rc = run_health(tmp_path, prefix="(5) backup: ")
    assert line.split(": ", 1)[1].startswith(verdict), line
    assert rc == (1 if verdict.startswith("FAIL") else 0), line


def run_health(tmp_path, prefix="(3) lake: ", host_registry=None, **env):
    top = tmp_path / "repo"
    top.mkdir()
    if host_registry is not None:
        (top / "config").mkdir()
        (top / "config/instruments.yaml").write_text(host_registry)
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
             "CURL_LOG": str(tmp_path / "curl.log"),
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


# Binance's capture against the host file, the other two serving no metrics
# (WARN). A capture not recreated, the reverse, an image older than the gauge and
# a value PyYAML would read as a boolean each FAIL.
@pytest.mark.parametrize("name, book, host, verdict", [
    ("both agree, books off", 0, "false", "PASS (books off: process off, host file off"),
    ("both agree, books on", 1, "true", "PASS (books on"),
    ("the registry says off, the capture was never recreated", 1, "false", "FAIL (process on"),
    ("the registry says on, the capture still runs off", 0, "true", "FAIL (process off"),
    ("no gauge: the image predates ADR-050", None, "true", "FAIL (capture-binance exports no"),
    ("a YAML 1.1 spelling", 1, "on", "FAIL (config/instruments.yaml book_capture.binance is ?on"),
])
def test_order_book_switch(tmp_path, name, book, host, verdict):
    line, rc = run_health(tmp_path, prefix="(4) binance book_capture: ", host_registry=registry(host),
                          METRICS_binance=capture(book))
    assert line.split(": ", 1)[1].startswith(verdict), (name, line)
    assert rc == (1 if verdict.startswith("FAIL") else 0), (name, line)
    if "never recreated" in name:
        assert "--no-deps capture-binance" in line, line


def test_a_capture_that_is_not_running_is_not_compared(tmp_path):
    line, rc = run_health(tmp_path, prefix="(4) kraken book_capture: ", host_registry=registry("false"),
                          CAPTURES="binance", METRICS_binance=capture(0))
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
    line, rc = run_health(tmp_path, prefix="(2) binance: ", **env)
    assert line.split(": ", 1)[1].startswith(verdict), (name, line)
    assert rc == (1 if verdict.startswith("FAIL") else 0), (name, line)


# ADR-057: the ClickHouse half is stood down visibly, one SKIP line naming the
# ADR, and nothing the script runs reaches ClickHouse.
def test_clickhouse_is_skipped_not_queried(tmp_path):
    line, rc = run_health(tmp_path, prefix="(2) clickhouse gold.trades: ")
    assert line.split(": ", 1)[1].startswith("SKIP (the ClickHouse hot tier is paused, ADR-057"), line
    assert rc == 0, line
    calls = (tmp_path / "docker.log").read_text().splitlines()
    assert any("/k2-capture healthcheck" in c for c in calls), calls
    assert not [c for c in calls if "clickhouse" in c.lower()], calls
