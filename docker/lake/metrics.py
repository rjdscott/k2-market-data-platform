#!/usr/bin/env python3
"""
Prometheus exporter for the v3 lake. Runs as the long-lived `lake-metrics`
service; `docker/prometheus/rules/lake-alerts.yml` reads everything it exports.

    python3 /opt/prefect/lake/metrics.py --serve
    python3 /opt/prefect/lake/metrics.py --self-check    # offline, no catalog

**Everything comes from Iceberg snapshot summaries.** Not from in-process
counters, for the same reason `docker/offload/metrics.py` does not use them:
Prefect runs each job in a short-lived subprocess that exits long before
Prometheus scrapes anything it counted. The snapshot summary is the durable
record of what the pipeline actually did, and the ingest already writes its
position there for the exactly-once contract — so these metrics are free.

**Read over the Iceberg REST API with urllib, not through PyIceberg.** One GET
per table returns the whole table metadata, snapshots and summaries included —
verified against Lakekeeper 0.13.3 on 2026-08-26:

    curl -s "$LK/catalog/v1/$PREFIX/namespaces/raw/tables/messages" | jq .metadata.snapshots[-1].summary

PyIceberg was the first attempt and does not fit: `load_table` needs a FileIO to
fetch `metadata.json` from S3, `FsspecFileIO` needs `s3fs` (absent), and
`PyArrowFileIO` costs 122 MB of RSS on `import` alone, measured in the Spark
image — against a 128 MB container limit, before doing any work. Reading the
metadata the catalog already serves needs no object-store client, no S3
credentials, and no dependency beyond the standard library.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import time
import urllib.request
from datetime import datetime, timezone

from prometheus_client import Gauge, start_http_server

logger = logging.getLogger(__name__)

# Same names and same defaults as docker/lake/spark_conf.py. Read here rather
# than imported from it because that module imports pyspark.
CATALOG_URI = os.environ.get("K2_LAKE_CATALOG_URI", "http://lakekeeper:8181/catalog")
WAREHOUSE = os.environ.get("K2_LAKE_WAREHOUSE", "k2")

# The filesystem the 80% disk alert watches. See docker/lake/README.md, "What
# the disk metric actually measures": on a Docker Desktop host this is the VM's
# thin-provisioned disk rather than the machine's, so the metric carries the
# path it measured as a label instead of claiming to be the host's.
DISK_PATH = os.environ.get("K2_LAKE_DISK_PATH", "/minio-data")

TABLES = {
    "raw.messages": ("raw", "messages"),
    "bronze.trades": ("bronze", "trades"),
    "bronze.book_snapshots_l2": ("bronze", "book_snapshots_l2"),
    "audit.checks": ("audit", "checks"),
}

INGEST_TABLE = "raw.messages"
CHECKS_TABLE = "audit.checks"

# Snapshot summary property names, duplicated from docker/lake/offsets.py for
# the same reason as the config above — that module is imported by the Spark
# jobs and this one is deliberately not.
JOB = "k2.job"
JOB_INGEST = "ingest"
MAX_KAFKA_TS = "k2.max-kafka-ts"
AUDIT_FAILURES = "k2.audit-failures"

ingest_lag = Gauge(
    "k2_lake_ingest_lag_seconds",
    "Age of the newest Kafka record in raw.messages, from the latest ingest snapshot",
)
commit_age = Gauge(
    "k2_lake_last_commit_age_seconds", "Seconds since this table last committed", ["table"]
)
rows_total = Gauge("k2_lake_rows_total", "Rows in the table's current snapshot", ["table"])
files_total = Gauge("k2_lake_files_total", "Data files in the table's current snapshot", ["table"])
bytes_total = Gauge("k2_lake_bytes_total", "Bytes in the table's current snapshot", ["table"])
added_records = Gauge(
    "k2_lake_added_records", "Rows added by the table's most recent commit", ["table"]
)
avg_file_bytes = Gauge(
    "k2_lake_avg_file_bytes",
    "Mean data-file size — the small-file signal compaction exists to fix",
    ["table"],
)
audit_failures = Gauge(
    "k2_lake_audit_failures_total", "Failed checks in the most recent maintenance run"
)
disk_used_ratio = Gauge(
    "k2_lake_disk_used_ratio",
    "Used fraction of the filesystem holding the MinIO data volume, as seen from this container",
    ["path"],
)
disk_free_bytes = Gauge("k2_lake_disk_free_bytes", "Free bytes on that same filesystem", ["path"])
scrape_errors = Gauge(
    "k2_lake_scrape_errors", "Tables the last refresh could not read (0 is healthy)"
)


def _get(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=15) as response:  # noqa: S310 - fixed internal host
        return json.load(response)


def catalog_prefix() -> str:
    """The warehouse's UUID path prefix, which every catalog path is rooted at.

    Lakekeeper hands it out as `defaults.prefix`; using the warehouse *name*
    instead answers 400 WarehouseIdIsNotUUID (the same trap
    docker/lake/init-lake.sh documents).
    """
    config = _get(f"{CATALOG_URI.rstrip('/')}/v1/config?warehouse={WAREHOUSE}")
    return config["defaults"]["prefix"]


def load_snapshots(prefix: str, namespace: str, table: str) -> list:
    """`[{snapshot}]` oldest first, straight out of the REST loadTable response."""
    url = f"{CATALOG_URI.rstrip('/')}/v1/{prefix}/namespaces/{namespace}/tables/{table}"
    return _get(url)["metadata"].get("snapshots", [])


def latest_ingest_summary(snapshots: list) -> dict:
    """Newest summary written by the ingest job.

    Compaction and expiry snapshots are skipped by the `k2.job` property the
    ingest sets — the same rule as docker/lake/offsets.py, and for the same
    reason: after a nightly compaction the newest snapshot on raw.messages is a
    rewrite that carries no offsets, and reading lag off it would report the
    lake as hours behind every morning.
    """
    for snapshot in reversed(snapshots):
        summary = snapshot.get("summary", {})
        if summary.get(JOB) == JOB_INGEST:
            return summary
    return {}


def _num(summary: dict, key: str) -> float:
    try:
        return float(summary.get(key, 0))
    except (TypeError, ValueError):
        return 0.0


def refresh(prefix: str, now: float) -> int:
    """Re-derive every metric. Returns the number of tables that could not be read."""
    errors = 0
    for label, (namespace, table) in TABLES.items():
        try:
            snapshots = load_snapshots(prefix, namespace, table)
        except Exception as exc:  # noqa: BLE001 - a missing table is a real state
            # Before `lake-ddl` has run, or after a table is dropped. Counted and
            # reported rather than crashing the loop: the other tables' metrics
            # are still worth serving, and LakeExporterDown must mean "the
            # exporter is gone", not "one table is".
            logger.warning("cannot read %s.%s: %s", namespace, table, exc)
            errors += 1
            continue

        if not snapshots:
            rows_total.labels(table=label).set(0)
            files_total.labels(table=label).set(0)
            continue

        current = snapshots[-1]
        summary = current.get("summary", {})
        files = _num(summary, "total-data-files")
        size = _num(summary, "total-files-size")

        commit_age.labels(table=label).set(max(0.0, now - current["timestamp-ms"] / 1000.0))
        rows_total.labels(table=label).set(_num(summary, "total-records"))
        files_total.labels(table=label).set(files)
        bytes_total.labels(table=label).set(size)
        added_records.labels(table=label).set(_num(summary, "added-records"))
        avg_file_bytes.labels(table=label).set(size / files if files else 0.0)

        if label == INGEST_TABLE:
            stamp = latest_ingest_summary(snapshots).get(MAX_KAFKA_TS)
            if stamp:
                ingest_lag.set(max(0.0, now - _epoch(stamp)))
        if label == CHECKS_TABLE:
            audit_failures.set(_num(summary, AUDIT_FAILURES))

    _refresh_disk()
    scrape_errors.set(errors)
    return errors


def _epoch(stamp: str) -> float:
    """`k2.max-kafka-ts` back to epoch seconds. Spark writes it with
    `Timestamp.isoformat()`, so it carries no zone suffix and is UTC by
    construction — reading it as local time would shift the lag by hours."""
    parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _refresh_disk() -> None:
    """Free space on the filesystem holding the MinIO data volume.

    `os.statvfs`, not ClickHouse's `FilesystemMainPath` series and not `mc du`.
    The ClickHouse gauge measures whatever filesystem its own data directory
    sits on and arrives with a name that invites reading it as the host's;
    `mc du` measures what the buckets hold, where the 80% alert asks what is
    left. See docker/lake/README.md for what this number does and does not cover
    on a Docker Desktop host.
    """
    try:
        stat = os.statvfs(DISK_PATH)
    except OSError as exc:
        logger.warning("cannot stat %s: %s", DISK_PATH, exc)
        return
    total = stat.f_blocks * stat.f_frsize
    free = stat.f_bavail * stat.f_frsize
    if total <= 0:
        return
    disk_used_ratio.labels(path=DISK_PATH).set((total - free) / total)
    disk_free_bytes.labels(path=DISK_PATH).set(free)


def serve(port: int = 8000, interval: int = 30) -> None:
    start_http_server(port)
    logger.info("lake metrics exporter listening on :%d/metrics", port)
    prefix = None
    while True:
        try:
            if prefix is None:
                prefix = catalog_prefix()
            refresh(prefix, time.time())
        except Exception as exc:  # noqa: BLE001 - an exporter must never exit
            # ponytail: re-resolve the prefix on any error. Lakekeeper is the
            # only dependency, so there is nothing finer-grained to handle.
            logger.warning("refresh failed: %s", exc)
            prefix = None
        time.sleep(interval)


def _self_check() -> None:
    """Offline check of the summary parsing and the lag arithmetic."""
    ingest = {
        "timestamp-ms": 1787750204280,
        "summary": {
            "operation": "append",
            JOB: JOB_INGEST,
            MAX_KAFKA_TS: "2026-08-26T12:50:00.289000",
            "total-records": "2895643",
            "total-data-files": "9",
            "total-files-size": "143178703",
            "added-records": "2895643",
        },
    }
    compaction = {"timestamp-ms": 1787750304280, "summary": {"operation": "replace"}}

    assert _num(ingest["summary"], "total-files-size") == 143178703.0
    assert _num(ingest["summary"], "missing-key") == 0.0
    assert _num({"total-records": "not a number"}, "total-records") == 0.0

    # A compaction snapshot committed after the ingest must not hide the
    # ingest's timestamp — that is the whole point of keying on k2.job.
    assert latest_ingest_summary([ingest, compaction])[MAX_KAFKA_TS] == "2026-08-26T12:50:00.289000"
    assert latest_ingest_summary([compaction]) == {}
    assert latest_ingest_summary([]) == {}

    assert _epoch("2026-08-26T12:50:00.289000") == 1787748600.289
    assert _epoch("2026-08-26T12:50:00.289000+00:00") == 1787748600.289
    print("self-check ok")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serve", action="store_true", help="run the HTTP exporter")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--interval", type=int, default=30, help="refresh seconds")
    parser.add_argument("--self-check", action="store_true", help="offline logic check")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if args.self_check:
        _self_check()
    elif args.serve:
        serve(port=args.port, interval=args.interval)
    else:
        parser.error("nothing to do: pass --serve or --self-check")
