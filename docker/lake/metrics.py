#!/usr/bin/env python3
"""
Prometheus exporter for the v3 lake. Runs as the long-lived `lake-metrics`
service; `docker/prometheus/rules/lake-alerts.yml` reads everything it exports.

    python3 /opt/prefect/lake/metrics.py --serve
    python3 /opt/prefect/lake/metrics.py --self-check    # offline, no catalog

**Everything comes from Iceberg snapshot summaries.** Not from in-process
counters, for the same reason the deleted `docker/offload/metrics.py` did not use them:
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
    # Phase E bronze per venue (docker/lake/bronze.py, ADR-026)
    "bronze.binance_trade": ("bronze", "binance_trade"),
    "bronze.binance_depth20": ("bronze", "binance_depth20"),
    "bronze.kraken_trade": ("bronze", "kraken_trade"),
    "bronze.kraken_book": ("bronze", "kraken_book"),
    "bronze.coinbase_market_trades": ("bronze", "coinbase_market_trades"),
    "bronze.coinbase_level2": ("bronze", "coinbase_level2"),
    "silver.trades_binance": ("silver", "trades_binance"),
    "silver.trades_kraken": ("silver", "trades_kraken"),
    "silver.trades_coinbase": ("silver", "trades_coinbase"),
    "gold.trades": ("gold", "trades"),
    "gold.ohlcv_1m": ("gold", "ohlcv_1m"),
    "audit.checks": ("audit", "checks"),
}

INGEST_TABLE = "raw.messages"
CHECKS_TABLE = "audit.checks"

# Snapshot summary property names, duplicated from docker/lake/offsets.py for
# the same reason as the config above — that module is imported by the Spark
# jobs and this one is deliberately not.
JOB = "k2.job"
JOB_INGEST = "ingest"
JOB_MAINTENANCE = "maintenance"
MAX_KAFKA_TS = "k2.max-kafka-ts"
KAFKA_BACKLOG = "k2.kafka-backlog"
AUDIT_FAILURES = "k2.audit-failures"
UNRESOLVABLE_IDS = "k2.unresolvable-schema-ids"
OFFSET_GAPS = "k2.offset-gaps"

# ── timestamps, not ages ────────────────────────────────────────────────────
#
# Every "how stale is X" metric here is exported as the INSTANT X last happened
# and aged in PromQL with `time() - <gauge>`. That is not a style preference: an
# age computed at scrape time is only recomputed on a *successful* read, so when
# Lakekeeper is down every age gauge freezes at its last small value and
# `> 1800` becomes unreachable — the exporter goes blind in exactly the outage
# whose backstop it is supposed to be. A timestamp ages by itself whether the
# exporter is reading, stuck, or wrong.
last_commit_ts = Gauge(
    "k2_lake_last_commit_ts_seconds",
    "Unix time of this table's newest commit. Age it with time() - this",
    ["table"],
)
max_kafka_ts = Gauge(
    "k2_lake_max_kafka_ts_seconds",
    "Unix time of the newest Kafka record in raw.messages, from the latest ingest snapshot. "
    "Lag is time() - this",
)
ingest_backlog = Gauge(
    "k2_lake_ingest_backlog_offsets",
    "Kafka records the last ingest deliberately left unread on this topic, summed over its "
    "partitions — what --max-offsets-per-partition capped off, not what the ingest is behind by",
    ["topic"],
)
last_compaction_ts = Gauge(
    "k2_lake_last_compaction_ts_seconds",
    "Unix time of this table's newest file-rewrite snapshot — the compaction job itself, "
    "not its side effect on mean file size",
    ["table"],
)
last_refresh_ts = Gauge(
    "k2_lake_last_refresh_ts_seconds",
    "Unix time this exporter last completed a full refresh. Frozen means the exporter is up "
    "and producing nothing — the fastest signal of a catalog outage",
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
unresolvable_schema_ids = Gauge(
    "k2_lake_unresolvable_schema_ids_total",
    "Schema ids the registry would not serve in the most recent stage-2 run",
)
offset_gaps = Gauge(
    "k2_lake_offset_gaps_total",
    "Partitions the most recent ingest resumed past evicted records, with --accept-data-loss. "
    "The durable record is the offset_gap row in lake.audit.checks; this gauge only says "
    "it just happened",
)
disk_used_ratio = Gauge(
    "k2_lake_disk_used_ratio",
    "Used fraction of the filesystem holding the MinIO data volume, as seen from this container",
    ["path"],
)
disk_free_bytes = Gauge("k2_lake_disk_free_bytes", "Free bytes on that same filesystem", ["path"])
scrape_errors = Gauge(
    "k2_lake_scrape_errors_total", "Tables the last refresh could not read (0 is healthy)"
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


def load_metadata(prefix: str, namespace: str, table: str) -> dict:
    """The table's Iceberg metadata document, straight out of REST loadTable."""
    url = f"{CATALOG_URI.rstrip('/')}/v1/{prefix}/namespaces/{namespace}/tables/{table}"
    return _get(url)["metadata"]


def current_snapshot(metadata: dict) -> dict:
    """The snapshot `current-snapshot-id` points at, or `{}`.

    Not `snapshots[-1]`. The array is metadata order, and the authoritative
    pointer is the id: after a rollback or a branch write the newest entry in
    the array is not the live snapshot, and every gauge derived from it would
    describe a snapshot no reader can see.
    """
    current_id = metadata.get("current-snapshot-id")
    if current_id is None or current_id == -1:
        return {}
    for snapshot in metadata.get("snapshots", []):
        if snapshot.get("snapshot-id") == current_id:
            return snapshot
    return {}


# Iceberg's `operation` for a file rewrite. `rewrite_data_files` does not go
# through `writeTo`, so it cannot carry a `k2.job` property the way the ingest
# does — the operation field is the only marker it leaves.
REWRITE_OPERATIONS = {"replace", "overwrite"}


def latest_compaction_ts(snapshots: list) -> float:
    """Unix time of the newest file-rewrite snapshot, or 0 if there is none."""
    stamps = [
        s.get("timestamp-ms", 0)
        for s in snapshots
        if s.get("summary", {}).get("operation") in REWRITE_OPERATIONS
    ]
    return max(stamps, default=0) / 1000.0


# How long the newest `k2.job=ingest` summary on audit.checks is taken to still
# describe the present. Three 5-minute ingest cycles.
INGEST_SUMMARY_MAX_AGE = 900.0


def latest_job_snapshot(snapshots: list, job: str) -> dict:
    """Newest snapshot written by `job`, keyed on the `k2.job` property.

    The same rule as docker/lake/offsets.py, and for the same reason: a table
    takes commits from more than one writer, and reading a gauge off whichever
    snapshot happens to be current reads whichever writer went last.

    On raw.messages that is compaction — after a nightly rewrite the newest
    snapshot carries no offsets, and reading lag off it would report the lake as
    hours behind every morning. On audit.checks it is the ingest: stage 2 files
    an `unresolvable_schema_id` row under `k2.job=ingest` and the nightly audit
    files its count under `k2.job=maintenance`, so a current-snapshot read lets
    an ingest row zero a firing LakeAuditFailed with no audit having passed.
    Both gauges therefore name the job whose number they claim to be.

    **Newest by `timestamp-ms`, not by position in the array.** This used to walk
    the list backwards, on the assumption that a metadata array is in commit
    order. Lakekeeper 0.13.3 does not promise that and does not deliver it:
    against `raw.messages` with five ingest snapshots, two successive REST
    loadTable calls minutes apart returned them in two different orders, and the
    gauges read a run that was two commits stale — `k2_lake_max_kafka_ts_seconds`
    reported lag that had already been closed. Same rule as
    docker/lake/offsets.py's `latest_summary`, which was always written this way
    because the Spark side had `committed_at` in front of it.
    """
    matching = [s for s in snapshots if s.get("summary", {}).get(JOB) == job]
    if not matching:
        return {}
    return max(matching, key=lambda s: s.get("timestamp-ms", 0))


def latest_job_summary(snapshots: list, job: str) -> dict:
    """That snapshot's summary, or `{}`."""
    return latest_job_snapshot(snapshots, job).get("summary", {})


def fresh_ingest_value(snapshots: list, key: str, now: float) -> float:
    """One ingest-filed finding count on audit.checks, or 0 once it is stale.

    The ingest commits a row to `audit.checks` **only when it found something**
    — a clean run writes nothing at all (`write_audit_rows` in
    docker/lake/ingest.py). So an ingest summary is not a statement about now;
    it is the last time something went wrong. Read as-is, the gauge latches:
    register the missing schema and the count still stands until the next
    unrelated ingest finding, which on a quiet week is never —
    `LakeUnresolvableSchemaId` would then stay firing for a fault that was fixed
    in minutes.

    Ageing it out is what makes the gauge readable in both directions. A genuine
    unserved schema id re-files the same row every 5-minute cycle, so the
    summary stays inside this window and the gauge holds above 0 indefinitely; a
    fixed one stops being re-filed, falls out of the window within 15 minutes,
    and the alert resolves on its own. An `offset_gap` is filed **once**, by the
    one `--accept-data-loss` run that repaired it, so its gauge is a ~15-minute
    pulse by construction and `LakeOffsetGap` is a notification rather than a
    condition. The durable record is the row.

    **Newest snapshot that carries THIS key**, not newest ingest snapshot. The
    two findings interleave on one table, and reading whichever ingest commit
    went last would let an offset_gap commit — which carries no schema-id count
    — read as "no unserved ids" and clear a firing alert.
    """
    matching = [
        s
        for s in snapshots
        if s.get("summary", {}).get(JOB) == JOB_INGEST and key in s.get("summary", {})
    ]
    if not matching:
        return 0.0
    newest = max(matching, key=lambda s: s.get("timestamp-ms", 0))
    if now - newest.get("timestamp-ms", 0) / 1000.0 > INGEST_SUMMARY_MAX_AGE:
        return 0.0
    return _num(newest.get("summary", {}), key)


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
            metadata = load_metadata(prefix, namespace, table)
        except Exception as exc:  # noqa: BLE001 - a missing table is a real state
            # Before `lake-ddl` has run, or after a table is dropped. Counted and
            # reported rather than crashing the loop: the other tables' metrics
            # are still worth serving, and LakeExporterDown must mean "the
            # exporter is gone", not "one table is".
            logger.warning("cannot read %s.%s: %s", namespace, table, exc)
            errors += 1
            continue

        snapshots = metadata.get("snapshots", [])
        current = current_snapshot(metadata)
        if not current:
            rows_total.labels(table=label).set(0)
            files_total.labels(table=label).set(0)
            continue

        summary = current.get("summary", {})
        files = _num(summary, "total-data-files")
        size = _num(summary, "total-files-size")

        last_commit_ts.labels(table=label).set(current["timestamp-ms"] / 1000.0)
        rows_total.labels(table=label).set(_num(summary, "total-records"))
        files_total.labels(table=label).set(files)
        bytes_total.labels(table=label).set(size)
        added_records.labels(table=label).set(_num(summary, "added-records"))
        avg_file_bytes.labels(table=label).set(size / files if files else 0.0)

        compacted = latest_compaction_ts(snapshots)
        if compacted:
            last_compaction_ts.labels(table=label).set(compacted)

        if label == INGEST_TABLE:
            ingest_summary = latest_job_summary(snapshots, JOB_INGEST)
            stamp = ingest_summary.get(MAX_KAFKA_TS)
            if stamp:
                max_kafka_ts.set(_epoch(stamp))
            for topic, remaining in _backlog(ingest_summary).items():
                ingest_backlog.labels(topic=topic).set(remaining)
        if label == CHECKS_TABLE:
            # Two writers, two gauges, neither read off `current`. The nightly
            # audit's count is the one LakeAuditFailed watches and only a later
            # maintenance run may clear it.
            audit_failures.set(
                _num(latest_job_summary(snapshots, JOB_MAINTENANCE), AUDIT_FAILURES)
            )
            # The second ingest-side check arrived (`offset_gap`,
            # --accept-data-loss), and with it the split the previous version of
            # this comment predicted: each finding is counted into its own
            # summary property and each gauge reads its own key, because a
            # shared `k2.audit-failures` would have each finding overwriting the
            # other's gauge.
            #
            # Both are aged, unlike the audit gauge above: a clean audit run
            # still commits a summary and so refreshes its own number, but a
            # clean ingest commits nothing here at all.
            unresolvable_schema_ids.set(fresh_ingest_value(snapshots, UNRESOLVABLE_IDS, now))
            offset_gaps.set(fresh_ingest_value(snapshots, OFFSET_GAPS, now))

    _refresh_disk()
    scrape_errors.set(errors)
    # Last, and only on the path that got this far. A frozen
    # k2_lake_last_refresh_ts_seconds is what makes "exporter up, catalog
    # unreachable" alertable — the prefix lookup in serve() throws before
    # refresh() is even called when Lakekeeper is down, so nothing else here
    # would move either.
    last_refresh_ts.set(now)
    return errors


def _backlog(summary: dict) -> dict:
    """`k2.kafka-backlog` as `{topic: records}`, or `{}`.

    Only ingest commits carry it, and only runs that committed something: a
    caught-up cycle writes no snapshot at all, so the gauge holds the last
    committing run's numbers. That is the honest reading — the backlog has not
    changed if nothing was ingested — and "the ingest stopped running" is
    `k2_lake_max_kafka_ts_seconds` ageing, which is where it belongs.

    Tolerant of a missing or malformed value for the same reason `_num` is: this
    is a metadata string nothing type-checks, and an exporter must not go dark
    over one bad property.
    """
    try:
        return {str(t): float(n) for t, n in json.loads(summary.get(KAFKA_BACKLOG, "{}")).items()}
    except (TypeError, ValueError, AttributeError):
        logger.warning("unparseable %s: %r", KAFKA_BACKLOG, summary.get(KAFKA_BACKLOG))
        return {}


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
        "snapshot-id": 111,
        "timestamp-ms": 1787750204280,
        "summary": {
            "operation": "append",
            JOB: JOB_INGEST,
            MAX_KAFKA_TS: "2026-08-26T12:50:00.289000",
            KAFKA_BACKLOG: '{"market.crypto.v3.raw.kraken":22189954,"market.crypto.v3.book.kraken":0}',
            "total-records": "2895643",
            "total-data-files": "9",
            "total-files-size": "143178703",
            "added-records": "2895643",
        },
    }
    compaction = {
        "snapshot-id": 222,
        "timestamp-ms": 1787750304280,
        "summary": {"operation": "replace"},
    }

    assert _num(ingest["summary"], "total-files-size") == 143178703.0
    assert _num(ingest["summary"], "missing-key") == 0.0
    assert _num({"total-records": "not a number"}, "total-records") == 0.0

    # A compaction snapshot committed after the ingest must not hide the
    # ingest's timestamp — that is the whole point of keying on k2.job.
    assert (
        latest_job_summary([ingest, compaction], JOB_INGEST)[MAX_KAFKA_TS]
        == "2026-08-26T12:50:00.289000"
    )
    assert latest_job_summary([compaction], JOB_INGEST) == {}
    assert latest_job_summary([], JOB_INGEST) == {}

    # Array order is not commit order. Lakekeeper returned five ingest snapshots
    # in a different sequence on two successive loadTable calls (2026-08-26), and
    # walking the list backwards read a two-commit-stale run — a lag gauge
    # reporting a backlog that had already been drained. Newest by timestamp-ms.
    older = {
        "snapshot-id": 555,
        "timestamp-ms": ingest["timestamp-ms"] - 60_000,
        "summary": {"operation": "append", JOB: JOB_INGEST, MAX_KAFKA_TS: "2026-08-26T12:49:00"},
    }
    assert latest_job_summary([ingest, older], JOB_INGEST)[MAX_KAFKA_TS] == ingest["summary"][MAX_KAFKA_TS]
    assert latest_job_summary([older, ingest], JOB_INGEST)[MAX_KAFKA_TS] == ingest["summary"][MAX_KAFKA_TS]
    # And the same for the compaction gauge, which read the array the same way.
    early_rewrite = {"snapshot-id": 666, "timestamp-ms": 1, "summary": {"operation": "replace"}}
    assert latest_compaction_ts([compaction, early_rewrite]) == 1787750304.280

    # The audit.checks regression: an ingest-written unresolvable_schema_id row
    # commits AFTER a failing maintenance run, so it is the current snapshot.
    # Read off `current`, its own count would set k2_lake_audit_failures_total
    # to 1 — and a clean one to 0, silently clearing a firing LakeAuditFailed.
    # Keyed on k2.job the two numbers stay separate.
    audit_run = {
        "snapshot-id": 333,
        "timestamp-ms": 1787750404280,
        "summary": {"operation": "append", JOB: JOB_MAINTENANCE, AUDIT_FAILURES: "2"},
    }
    ingest_row = {
        "snapshot-id": 444,
        "timestamp-ms": 1787750504280,
        "summary": {
            "operation": "append",
            JOB: JOB_INGEST,
            AUDIT_FAILURES: "1",
            UNRESOLVABLE_IDS: "1",
        },
    }
    checks = [audit_run, ingest_row]
    assert _num(latest_job_summary(checks, JOB_MAINTENANCE), AUDIT_FAILURES) == 2.0
    assert _num(latest_job_summary(checks, JOB_INGEST), AUDIT_FAILURES) == 1.0
    # No ingest row yet: the ingest gauge is 0 and the audit gauge is untouched.
    assert _num(latest_job_summary([audit_run], JOB_INGEST), AUDIT_FAILURES) == 0.0
    assert _num(latest_job_summary([audit_run], JOB_MAINTENANCE), AUDIT_FAILURES) == 2.0

    # The latch that ageing fixes. Stage 2 writes an ingest row ONLY when it
    # found an unresolvable id, so once the schema is registered nothing
    # overwrites this summary — read without a freshness test the gauge would
    # still say 1 a week later, holding LakeUnresolvableSchemaId firing on a
    # fault that was fixed in minutes.
    filed = ingest_row["timestamp-ms"] / 1000.0
    assert fresh_ingest_value(checks, UNRESOLVABLE_IDS, filed) == 1.0
    # Still re-filed inside the window: a genuine unserved id, every 5 minutes.
    assert fresh_ingest_value(checks, UNRESOLVABLE_IDS, filed + INGEST_SUMMARY_MAX_AGE - 1) == 1.0
    # Two cycles missed and then a third: registered, nothing re-filed, clear.
    assert fresh_ingest_value(checks, UNRESOLVABLE_IDS, filed + INGEST_SUMMARY_MAX_AGE + 1) == 0.0
    # The audit gauge is NOT aged — a clean maintenance run commits its own
    # summary, so its number refreshes itself and must survive a stale ingest.
    assert _num(latest_job_summary(checks, JOB_MAINTENANCE), AUDIT_FAILURES) == 2.0
    # No ingest row has ever been filed: 0, not an exception.
    assert fresh_ingest_value([audit_run], UNRESOLVABLE_IDS, filed) == 0.0

    # Two ingest-side findings on one table. The offset_gap commit is NEWER and
    # carries no schema-id count; read off "the newest ingest snapshot" it would
    # report 0 unserved ids and clear a firing LakeUnresolvableSchemaId while
    # the id is still unserved. Each gauge reads the newest snapshot carrying
    # its own key, so neither finding can speak for the other.
    gap_row = {
        "snapshot-id": 777,
        "timestamp-ms": ingest_row["timestamp-ms"] + 30_000,
        "summary": {"operation": "append", JOB: JOB_INGEST, AUDIT_FAILURES: "2", OFFSET_GAPS: "2"},
    }
    both_findings = [audit_run, ingest_row, gap_row]
    filed_gap = gap_row["timestamp-ms"] / 1000.0
    assert fresh_ingest_value(both_findings, OFFSET_GAPS, filed_gap) == 2.0
    assert fresh_ingest_value(both_findings, UNRESOLVABLE_IDS, filed_gap) == 1.0
    # And the gap gauge is a pulse: it is filed once, by the one repair run, so
    # it ages out 15 minutes later and LakeOffsetGap resolves itself. The
    # offset_gap ROW in audit.checks is the durable record, not this number.
    assert fresh_ingest_value(both_findings, OFFSET_GAPS, filed_gap + INGEST_SUMMARY_MAX_AGE + 1) == 0.0
    assert fresh_ingest_value(checks, OFFSET_GAPS, filed) == 0.0

    # The backlog gauge. A drained topic must report 0 rather than vanish from
    # the map — an absent series and a zero one read very differently on a
    # dashboard, and "no backlog" is the answer we want stated.
    backlog = _backlog(ingest["summary"])
    assert backlog["market.crypto.v3.raw.kraken"] == 22189954.0
    assert backlog["market.crypto.v3.book.kraken"] == 0.0
    # A compaction summary carries no backlog, and a malformed one is not fatal:
    # the property is an unchecked string in a metadata map.
    assert _backlog(compaction["summary"]) == {}
    assert _backlog({KAFKA_BACKLOG: "not json"}) == {}
    assert _backlog({KAFKA_BACKLOG: "[1, 2]"}) == {}

    assert _epoch("2026-08-26T12:50:00.289000") == 1787748600.289
    assert _epoch("2026-08-26T12:50:00.289000+00:00") == 1787748600.289

    # `current-snapshot-id`, not the tail of the array. Flip the id back to the
    # ingest and the newest entry must stop being the answer, or every gauge
    # would describe a snapshot no reader can see after a rollback.
    both = {"snapshots": [ingest, compaction], "current-snapshot-id": 222}
    assert current_snapshot(both) is compaction
    assert current_snapshot({**both, "current-snapshot-id": 111}) is ingest
    assert current_snapshot({"snapshots": [ingest], "current-snapshot-id": -1}) == {}
    assert current_snapshot({}) == {}

    # The compaction gauge measures the rewrite job, not its side effect.
    assert latest_compaction_ts([ingest, compaction]) == 1787750304.280
    assert latest_compaction_ts([ingest]) == 0.0
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
