#!/usr/bin/env python3
"""
K2 v3 — the one Spark session builder for the Iceberg lake.

Every Spark job on the lake gets its session from `lake_session()`, so the
`lake` catalog config lives here and nowhere else. It is the only catalog on the
stack: v2's `k2` hadoop catalog and the offload that built its own session went
in Phase D. The old ingest and maintenance jobs that used it are retired and
archived in legacy/v3-lake-ingest/ (ADR-056); much of the sizing below is their
record.

Smoke test (the runnable check for this file):

    docker exec k2-spark-iceberg python3 /home/iceberg/lake/spark_conf.py --smoke
"""

import os
import sys

from pyspark.sql import SparkSession

# Every endpoint, region, path-style flag and catalog URI is read from the
# environment with today's single-host value as the default. That is not
# speculative config: it is the requirement from
# docs/research/2026-08-26-v3-requirements-clarification.md Q9 — moving this
# lake to S3 + Glue/Lakekeeper-on-ECS must be a change to the environment, not a
# change to these files. Defaults keep `python3 spark_conf.py --smoke` working
# with no environment at all.
#
# The defaults must match LK / WAREHOUSE / BUCKET in docker/lake/init-lake.sh,
# which reads the same four names with the same defaults. That script creates
# what this one connects to; override one side only and every job points at a
# catalog nothing bootstrapped.
CATALOG = os.environ.get("K2_LAKE_CATALOG", "lake")
CATALOG_URI = os.environ.get("K2_LAKE_CATALOG_URI", "http://lakekeeper:8181/catalog")
WAREHOUSE = os.environ.get("K2_LAKE_WAREHOUSE", "k2")
S3_ENDPOINT = os.environ.get("K2_S3_ENDPOINT", "http://minio:9000")
S3_REGION = os.environ.get("K2_S3_REGION", "local-01")
# Path-style on MinIO, virtual-hosted on real S3. String, not bool: it goes
# straight into a Spark conf value.
S3_PATH_STYLE = os.environ.get("K2_S3_PATH_STYLE", "true")

# Redpanda's schema registry. `catalog.fetch_schema` reads Avro schemas by id from here.
SCHEMA_REGISTRY_URL = os.environ.get("K2_SCHEMA_REGISTRY_URL", "http://redpanda:8081")
KAFKA_BROKERS = os.environ.get("K2_BROKERS", "redpanda:9092")

# Driver heap, pinned rather than inherited. What follows down to "The trigger
# fired" is the 2026-08 sizing, kept as the record of why 768m was chosen: the
# container was capped at 2 CPU and 4 GiB then (3 CPU and 8 GiB now), and the
# default is no longer 768m. `spark-iceberg` was capped at 2 CPU
# and 4 GiB, and that budget is NOT empty before a job starts: the base image
# runs a standalone Master, a Worker, the History Server, a Thrift Server and
# Jupyter for the whole life of the container. Idle, with no driver at all,
# that is 633 MiB — `docker stats --no-stream k2-spark-iceberg`, 2026-08-26
# (635.4 MiB on a re-read 2026-08-27). Every sum below starts from it.
#
# Two drivers can be alive in the container at once, and the cron does not
# prevent it. `lake-ingest-5min` is `1-59/5` and `lake-maintenance-daily` is
# `0 3 * * *`, so the two never start on the same minute — but 03:00 and 03:01
# are one minute apart and a compaction run outlives that by a wide margin.
# An operator's `docker exec` during an incident is the other way to get two.
#
# 768m each is what fits. A driver JVM costs its heap plus roughly 400-600 MB
# of metaspace, code cache, GC structures, thread stacks and direct buffers,
# and each run carries a Python driver process on top:
#
#     2 x (768 + ~550) + ~400 Python + 633 baseline  = ~3.58 GiB of the 4 GiB
#     2 x (1024 + ~550) + ~400 Python + 633 baseline = ~4.08 GiB — over the cap
#
# The second line is the image's own default, and it is the arithmetic this
# setting exists to fix: inheriting 1 g puts two drivers plus the always-on
# JVMs past 4 GiB, and the failure mode is an OOM-kill of whichever driver
# asks for the last page — which reads as a random ingest failure rather than
# as a memory problem.
#
# **Measured, 2026-08-27.** The estimate above held. Four hand-run ingests
# against the live 41.5 M-record backlog, sampled every 2 s with `docker stats`
# and `ps -o rss=,cmd= -A` in the container:
#
#     bound 1,000   77,542 rows   17 s   peak driver RSS 1,122 MiB
#     bound 50,000  2,721,812     92 s   peak driver RSS 1,243 MiB  <- the number
#     bound 50,000  1,770,914     57 s   peak driver RSS 1,227 MiB
#     bound 50,000  1,564,334     49 s   peak driver RSS 1,221 MiB
#
# Peak container memory was 2.13 GiB of 4 GiB at the worst of those, over a
# 633 MiB idle baseline. 1,243 MiB is 768m of heap plus ~475 MiB of metaspace,
# code cache, GC structures, thread stacks and direct buffers — inside the
# ~550 MiB the arithmetic below assumed, so two concurrent drivers still fit.
#
# The number that matters is not its size but its FLATNESS: 35x the batch moved
# it 11%. That is what "nothing payload-bearing is cached" buys, and it is why
# `--max-offsets-per-partition` can be raised for a backlog drain without
# re-deriving this figure. Peak RSS here is a function of the heap setting, not
# of how much arrived.
#
# Revisit trigger (2026-08, fired 2026-11-25, see below): raise it back to `1g` if `lake-ingest-5min` fails with an
# OOM-kill or `java.lang.OutOfMemoryError`. It did exactly that once, on
# 2026-08-26, and the cause was a `persist(DISK_ONLY)` on a DataFrame carrying
# 5.2 MB payload rows rather than an undersized heap. Read the archived
# legacy/v3-lake-ingest/lake/README.md, "What one run may read, and why nothing
# is cached", before touching this value.
#
# The other consumer was the `lake-ddl` one-shot (retired with the pipeline), in
# its own container with a 1 GiB limit: too small for 768m plus JVM overhead, so
# docker-compose.yml set `K2_LAKE_DRIVER_MEMORY: 512m` for that service only.
#
# **The trigger fired, 2026-11-25: 768m to 1536m.** From 17:12 UTC the ingest died of
# `java.lang.OutOfMemoryError: Java heap space` on most first attempts: 10 driver runs
# with a failed stage among the History Server's last 40, and two DAG runs failed on
# both tries. Each failed in the same place, the sort under the aggregate that feeds
# the event-bars append in stage 2d (`UnsafeSorterSpillReader.<init>`; the failed
# stage is `append at DataFrameWriterV2`, `curl localhost:18080/api/v1/applications/
# <id>/stages?status=failed` in the container). Stage 2d recomputed every touched
# symbol-day of bars from all of that day's canonical trades, and the failed stages'
# shuffle read was the size of the whole UTC day so far. So the input grows from
# 00:00 UTC to 24:00 whatever the batch size, and is largest on a busy day's evening.
# Nothing payload-bearing is cached; this is not the 2026-08-26 failure.
#
# `1g` above was written when the container was capped at 4 GiB. It is 8 GiB now and
# the maintenance driver held the ingest lock, so 1536m fits with room: measured on
# the next run, by DAG param before this default moved (a trigger of the retired
# ingest DAG with `--conf '{"driver_memory": "1536m"}'`): one attempt, 253 s, no
# OutOfMemoryError, peak driver RSS 2,259 MiB sampled every 20 s with `ps -o rss=` in
# the container. The scheduled run before it, at 768m, failed on both tries.
#
# Revisit trigger: `OutOfMemoryError` in an ingest log again, or peak driver RSS above
# 3 GiB. More heap buys time only: the bars recompute is linear in the day's trades,
# and the fix for that is an incremental recompute, not a third raise.
DRIVER_MEMORY = os.environ.get("K2_LAKE_DRIVER_MEMORY", "1536m")


# The engine key a session signs with, by the NAMES of its two variables: Spark's own unless a
# caller names another (expire.py names the maintenance key). Each engine's MinIO user holds only
# its policy (docker/lake/minio/policies/), and with remote signing off that key is what MinIO checks.
SPARK_KEY = ("K2_SPARK_S3_ACCESS_KEY", "K2_SPARK_S3_SECRET_KEY")
BUILD_SUFFIX = "_build"  # k2gen model.build_catalog: <domain>_build writes warehouse <domain>


def warehouse_of(catalog: str) -> str:
    """The warehouse a Spark catalog opens: `lake` the lake's own, `<domain>_build` the domain's."""
    if catalog == CATALOG:
        return WAREHOUSE
    if catalog.endswith(BUILD_SUFFIX) and len(catalog) > len(BUILD_SUFFIX):
        return catalog.removesuffix(BUILD_SUFFIX)
    raise ValueError(f"{catalog!r} is neither {CATALOG!r} nor a <domain>{BUILD_SUFFIX} catalog")


def lake_session(app_name: str, driver_memory: str = DRIVER_MEMORY, key: tuple[str, str] = SPARK_KEY,
                 catalogs: list[str] | None = None) -> SparkSession:
    """Spark session wired to the Lakekeeper REST catalog over MinIO: one Iceberg catalog per name
    in `catalogs` (default the `lake` catalog alone), each over its warehouse (warehouse_of), each
    signing with the pair whose variable names `key` gives. The values go into the session's conf
    only, which the image redacts (tests/test_spark_redaction.py), and are never printed."""
    access_key, secret_key = os.environ[key[0]], os.environ[key[1]]
    names = catalogs or [CATALOG]

    builder = (
        SparkSession.builder.appName(app_name)
        # Read by pyspark BEFORE it launches the gateway JVM, so this really is
        # the heap and not just a reported value: with 1536m set here the driver
        # reports maxMemory() == 1536 MiB in this image (verified 2026-08-26).
        .config("spark.driver.memory", driver_memory)
        .config(
            "spark.sql.extensions",
            "org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions",
        )
    )
    for name in names:
        builder = (
            builder.config(f"spark.sql.catalog.{name}", "org.apache.iceberg.spark.SparkCatalog")
            .config(f"spark.sql.catalog.{name}.type", "rest")
            .config(f"spark.sql.catalog.{name}.uri", CATALOG_URI)
            .config(f"spark.sql.catalog.{name}.warehouse", warehouse_of(name))
            .config(f"spark.sql.catalog.{name}.io-impl", "org.apache.iceberg.aws.s3.S3FileIO")
            .config(f"spark.sql.catalog.{name}.s3.endpoint", S3_ENDPOINT)
            .config(f"spark.sql.catalog.{name}.s3.path-style-access", S3_PATH_STYLE)
            .config(f"spark.sql.catalog.{name}.s3.access-key-id", access_key)
            .config(f"spark.sql.catalog.{name}.s3.secret-access-key", secret_key)
            .config(f"spark.sql.catalog.{name}.s3.region", S3_REGION)
        )
    return (
        builder.config("spark.sql.defaultCatalog", CATALOG if CATALOG in names else names[0])
        # Pinned, not inherited from the image's TZ. The archived maintenance
        # job showed why: its expiry built `older_than => TIMESTAMP '...'` from
        # a UTC datetime, which Spark interprets in the session zone, while its
        # compaction predicate used `timestamp_seconds(<epoch>)`, which is
        # absolute. One non-UTC session zone and expiry silently shifts by the
        # offset while compaction does not. A naive timestamp read in any job
        # means UTC because of this line. The image happens to be Etc/UTC
        # today; this makes it a contract instead of a coincidence.
        .config("spark.sql.session.timeZone", "UTC")
        # Case-sensitive column resolution, for one reason: Binance's trade
        # payload carries both `e` (event type) and `E` (event time), and a
        # table that keeps the venue's field names as sent (the old Binance
        # trade frames table, retired) holds both. With the default
        # (insensitive) resolver the two are one duplicate column and the
        # CREATE TABLE fails. Every other lake column is lowercase and
        # referenced as written, so nothing else notices the switch.
        .config("spark.sql.caseSensitive", "true")
        # LAST_WIN, not the default EXCEPTION. `map_from_entries` over Kafka
        # headers throws on a duplicate key, and it throws on the *archive*
        # write: one foreign producer sending two headers of the same name
        # would block the archive at that offset forever, which the old
        # archive's DDL promised could not happen. capture sets exactly one header
        # (services/capture-rust/src/sink.rs); keeping the last of a duplicate
        # pair loses nothing we wrote and costs nothing we did.
        .config("spark.sql.mapKeyDedupPolicy", "LAST_WIN")
        # 256 rows per columnar cache batch, not the default 10,000. A backstop,
        # deliberately: no lake job caches a payload-bearing DataFrame (the
        # archived ingest, legacy/v3-lake-ingest/lake/ingest.py, says why at
        # each site), so this setting is not expected to be reached.
        #
        # It is here because the failure it bounds was not obvious from the code
        # that caused it. `persist(DISK_ONLY)` reads as "spill to disk"; Spark
        # actually builds an in-memory columnar batch first and writes it out
        # whole, so the heap high-water mark is batchSize x row size regardless
        # of the storage level. At 10,000 rows of Coinbase level2 frames — up to
        # 5.2 MB each — that is tens of gigabytes against a 768m heap, and it is
        # exactly how the first cold start died. At 256 the same mistake costs
        # ~1.3 GB: still too much, but it fails on a batch boundary with a
        # readable trace instead of taking the JVM out mid-append.
        #
        # NOT touched, for the record: `spark.sql.files.maxRecordsPerFile` and
        # the tables' `write.target-file-size-bytes`. Neither was implicated —
        # they govern how the WRITER rolls output files, which was streaming
        # correctly the whole time, and shrinking them would trade a memory
        # problem this does not have for a small-file problem compaction would
        # then have to clean up nightly.
        .config("spark.sql.inMemoryColumnarStorage.batchSize", "256")
        .getOrCreate()
    )


def _smoke() -> None:
    """Round-trip one row through the catalog: create, append, read, drop.

    Asserts the snapshot summary carries the write's `snapshot-property.*` — that
    is the mechanism ADR-018 relies on to store Kafka offsets atomically with the
    data commit, so it is the part worth proving before Phase C builds on it.
    """
    spark = lake_session("k2-lake-smoke")
    # Its own namespace, dropped with the table: a smoke leaves the lake as it found it.
    namespace = f"{CATALOG}.smoke"
    table = f"{namespace}.t"
    # try/finally so a failed assert still drops the table and stops the session.
    # Without it the first failure leaves the table behind, and the NEXT run's
    # CREATE TABLE fails on the leftover instead of on the real problem: a
    # debugging dead end on the very path you only walk when something is wrong.
    try:
        # No PURGE on the drops: Lakekeeper 0.13.3 answers a purge-drop with
        # `BadRequestException: Table does not exist ... at location <metadata.json>`
        # (verified 2026-08-26). It expires dropped tables through its own task queue,
        # so a plain DROP is both what works and what we want.
        spark.sql(f"CREATE NAMESPACE IF NOT EXISTS {namespace}")
        spark.sql(f"DROP TABLE IF EXISTS {table}")
        spark.sql(f"CREATE TABLE {table} (id bigint, note string) USING iceberg")

        (
            spark.createDataFrame([(1, "smoke")], "id bigint, note string")
            .writeTo(table)
            .option("snapshot-property.k2.smoke", "1")
            .append()
        )

        count = spark.sql(f"SELECT count(*) FROM {table}").collect()[0][0]
        summary = spark.sql(
            f"SELECT summary FROM {table}.snapshots "
            "ORDER BY committed_at DESC LIMIT 1"
        ).collect()[0][0]

        print(f"count={count}")
        print(f"summary={summary}")

        assert count == 1, f"expected 1 row, got {count}"
        assert summary.get("k2.smoke") == "1", f"snapshot summary lost k2.smoke: {summary}"

        print("✓ lake smoke passed")
    finally:
        try:
            spark.sql(f"DROP TABLE IF EXISTS {table}")
            spark.sql(f"DROP NAMESPACE IF EXISTS {namespace}")
        finally:
            spark.stop()


if __name__ == "__main__":
    if "--smoke" not in sys.argv[1:]:
        print(__doc__)
        sys.exit(2)
    _smoke()

